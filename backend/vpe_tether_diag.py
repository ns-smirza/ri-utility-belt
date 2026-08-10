#!/usr/bin/env python3
"""
VPE Tethering Diagnostic Script
================================

Read-only diagnostic tool for a Netskope VPE appliance's tethering lifecycle.

USAGE
    python3 vpe_tether_diag.py <VPE-IP> [options]

WHAT IT DOES
    1. SSHes into the VPE (nsadmin / nsappliance by default, matching ~/.login_ssh.sh).
    2. Runs a single read-only Python collector ON the box that gathers:
       /opt/ns/appliance/status.json (the primary tethering-status source),
       cfg/cloud/remote endpoint files, kubectl pod state + events + describe,
       openssl cert parse, nslookup, and the key log signatures from
       nsclib / heartbeat_sync / diagnostic-agent / cfgwatcher-cloudsync /
       statsite + the cfgagent & callhome pod logs.
    3. Locally classifies which of the 4 known scenarios the box is in:
         S1 = fresh, never tethered
         S2 = first tether (tethered, no prior reset)
         S3 = deprovisioned (stale config from a prior tether)
         S4 = re-tethered (tethered again after a deprovision)
         FAILING/IN-PROGRESS = enrollment attempted but not all phases complete
         UNKNOWN             = state doesn't match any modeled scenario
    4. Prints a chronological list of every expected tethering state with:
         [✓] tick        = success (state achieved)
         [✗] cross       = expected but NOT achieved (a failure)
         [⚠] exclamation = ignorable / not-expected-for-this-scenario
       The first [✗] in a failing run is flagged as the root-cause stage.
    5. Prints a one-line SUMMARY (SUCCESS / FRESH / DEPROVISIONED / FAILING at stage X).

DESIGN PRINCIPLES
    * Diagnose only. NEVER mutates (no set/save/reset/restart). Read-only commands.
    * The "truth" for tethering status is /opt/ns/appliance/status.json .tethering_status
      (byte-identical to `nsshell status tethering` and reliable non-interactively,
      unlike `nsshell -c` which pages/hangs without a tty).
    * Expected log strings are shown with placeholders filled from the box's own
      values (serial, fqdn, did, identifier, tenant_id) so the human sees exactly
      what was expected vs. what is present.
    * Tethering is expected to complete in <=30 min (ideally 15). For a FAILING
      box, the enrollment age (from registration_token.json.created_at or
      client.pem notBefore) decides "IN PROGRESS (<30min grace)" vs
      "FAILING/TIMEOUT (>=30min)".
    * Only the 4 POSITIVE scenarios (S1-S4) are modeled. A state that doesn't
      fit is reported UNKNOWN with raw markers, not forced into a bucket.

REFERENCE
    The check list below is a direct encoding of the "Log String Registry" and
    per-scenario checklists in vpe_tethering_diagnostics.md, which was built and
    cross-validated on two physical appliances across S1/S2/S3/S4.
"""

import argparse
import datetime as _dt
import json
import os
import re
import shutil
import subprocess
import sys

# ---------------------------------------------------------------------------
# SSH defaults (match ~/.login_ssh.sh). Overridable via CLI / env.
# ---------------------------------------------------------------------------
DEFAULT_USER = os.environ.get("VPE_SSH_USER", "nsadmin")
DEFAULT_PASS = os.environ.get("VPE_SSH_PASS", "nsappliance")
SSH_CONNECT_TIMEOUT = "10"  # seconds to establish TCP
SSH_CMD_TIMEOUT = 90  # seconds for the whole remote collection
TETHER_GRACE_MIN = 30  # <30 min incomplete == "in progress"; >=30 == "failing/timeout"


# ===========================================================================
# REMOTE COLLECTOR
# ---------------------------------------------------------------------------
# This Python3 program is piped to the VPE over SSH and executed there as
# `sudo -n python3 -` (nsadmin has passwordless sudo; -n avoids any password
# prompt hang). It performs ONLY read-only actions and prints one JSON object
# on stdout. Every section is wrapped so a single missing file/command cannot
# abort the whole collection.
# ===========================================================================
REMOTE_COLLECTOR = r'''
import json, os, re, subprocess

def run(cmd):
    """Run a shell command (already includes sudo -n if needed), return stdout string."""
    try:
        p = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=25)
        return p.stdout or ""
    except Exception:
        return ""

def read_file(path):
    try:
        with open(path, errors="replace") as f:
            return f.read()
    except Exception:
        return None

def read_json(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return None

def exists(path):
    return os.path.exists(path)

def fsize(path):
    try:
        return os.path.getsize(path)
    except Exception:
        return None

def grep_all(path, pattern, cap=60):
    """Return up to the last `cap` lines in `path` matching `pattern` (compiled with re).

    We capture MANY matches (not just the last one) because a box that has been
    tethered+deprovisioned multiple times accumulates success lines from EVERY
    prior cycle. The LOCAL evaluator parses each line's timestamp and filters to
    the CURRENT cycle (timestamp >= current enrollment, and serial == current
    serial where the line carries one). Returning the last `cap` matches is
    enough to cover the current cycle plus a few prior ones for context.
    """
    rx = re.compile(pattern)
    out = []
    try:
        with open(path, errors="replace") as f:
            for line in f:
                if rx.search(line):
                    out.append(line.rstrip("\n"))
                    if len(out) > cap:
                        out = out[-cap:]
    except Exception:
        pass
    return out

def fmtime(path):
    """File mtime as epoch seconds (or None). Used for current-cycle freshness."""
    try:
        return int(os.path.getmtime(path))
    except Exception:
        return None

def _g(text, pattern, cap=60):
    """Grep a multi-line STRING (not a file) — last `cap` matching lines."""
    rx = re.compile(pattern)
    out = []
    for line in (text or "").splitlines():
        if rx.search(line):
            out.append(line.rstrip("\n"))
            if len(out) > cap:
                out = out[-cap:]
    return out

# kubectl helper (root via sudo -n; kubeconfig is root-readable).
KC = "sudo -n kubectl --kubeconfig /etc/rancher/k3s/k3s.yaml"

out = {}

# ---- build identity -------------------------------------------------------
out["build"] = {
    "version": (read_file("/opt/ns/cfg/nscli_config.version") or "").strip(),
    "platform_code": (read_file("/opt/ns/cfg/platform_code") or "").strip(),
    "hostname": (read_file("/etc/hostname") or "").strip() or "ns-vpe",
}

# ---- primary status source: /opt/ns/appliance/status.json -----------------
# .tethering_status is byte-identical to `nsshell status tethering`.
st = read_json("/opt/ns/appliance/status.json") or {}
out["status"] = st  # full object (tethering_status, reachability_status, rest-api-token, service_status, system_status)

# ---- cfgagent on-disk state files ----------------------------------------
# cfgagent_current_status is the CURRENT truth; cfgagent_connect_status can
# lag with a stale "connected" on a fresh box (verified S1 re-validation).
out["cfgagent_state"] = {
    "current": read_json("/opt/ns/states/lccloudsync/cfgagent_current_status"),
    "connect": read_json("/opt/ns/states/lccloudsync/cfgagent_connect_status"),
}

# ---- /opt/ns/cfg tethering-relevant files (+ mtimes for cycle freshness) --
# mtimes are captured so the local evaluator can require a file to have been
# (re)written during the CURRENT cycle (mtime >= cycle_start). This catches
# stale files left over from a prior tether that deprovision didn't clean.
cfgagent_conf = read_file("/opt/ns/cfg/cfgagent.conf") or ""
client_pem_openssl = ""
if exists("/opt/ns/cfg/client.pem"):
    client_pem_openssl = run("openssl x509 -in /opt/ns/cfg/client.pem -noout -subject -issuer -dates 2>/dev/null")
out["cfg"] = {
    "cfgagent_conf": cfgagent_conf,
    "cfgagent_conf_mtime": fmtime("/opt/ns/cfg/cfgagent.conf"),
    "system_json": read_json("/opt/ns/cfg/system.json"),
    "system_json_mtime": fmtime("/opt/ns/cfg/system.json"),
    "registration_token": read_json("/opt/ns/cfg/registration_token.json"),  # root-only; we run as root
    "registration_token_mtime": fmtime("/opt/ns/cfg/registration_token.json"),
    "cloudserial": read_json("/opt/ns/cfg/cloudserial.json"),
    "cloudserial_mtime": fmtime("/opt/ns/cfg/cloudserial.json"),
    "cloudconfig_size": fsize("/opt/ns/cfg/cloudconfig.json"),
    "cloudconfig_mtime": fmtime("/opt/ns/cfg/cloudconfig.json"),
    "config_json": read_json("/opt/ns/cfg/config.json"),
    "recon_status": read_json("/opt/ns/cfg/recon_status.json"),
    "ais_cert": read_json("/opt/ns/cfg/ais_cert.json"),
    "client_pem_exists": exists("/opt/ns/cfg/client.pem"),
    "client_pem_mtime": fmtime("/opt/ns/cfg/client.pem"),
    "client_key_exists": exists("/opt/ns/cfg/client.key"),
    "issuer_ca_exists": exists("/opt/ns/cfg/issuer_ca.pem"),
    "client_pem_openssl": client_pem_openssl,
}

# ---- /opt/ns/cloud source files (populated by configdist at stage 2) -----
out["cloud"] = {
    "serial": read_json("/opt/ns/cloud/serial"),
    "serial_mtime": fmtime("/opt/ns/cloud/serial"),
    "sfconfig_size": fsize("/opt/ns/cloud/sfconfig.json"),
    "sfconfig_mtime": fmtime("/opt/ns/cloud/sfconfig.json"),
    "rest_api_token": read_json("/opt/ns/cloud/rest_api_token.json"),
    "rest_api_token_mtime": fmtime("/opt/ns/cloud/rest_api_token.json"),
    "pushed_size": fsize("/opt/ns/cloud/pushed.json"),
    "pushed_mtime": fmtime("/opt/ns/cloud/pushed.json"),
}

# ---- remote endpoint files /opt/ns/appliance/common/remote/ --------------
remote_dir = "/opt/ns/appliance/common/remote"
rfiles = {}
if os.path.isdir(remote_dir):
    for fn in os.listdir(remote_dir):
        p = os.path.join(remote_dir, fn)
        if os.path.isfile(p):
            rfiles[fn] = read_file(p)
out["remote"] = {
    "files": rfiles,
    "callhomeservice_exists": exists(remote_dir + "/callhomeservice"),
    "callhomeservice": read_file(remote_dir + "/callhomeservice"),
    "callhomeservice_mtime": fmtime(remote_dir + "/callhomeservice"),
    "configdist_core_pub": read_file(remote_dir + "/configdist-core-pub"),
    "messaging": read_file(remote_dir + "/messaging"),
    "messaging_mtime": fmtime(remote_dir + "/messaging"),
}

# ---- tenant / content dirs -----------------------------------------------
def dir_file_count(path):
    try:
        return len([x for x in os.listdir(path) if os.path.isfile(os.path.join(path, x))])
    except Exception:
        return 0

tenant_dirs = []
if os.path.isdir("/opt/ns/tenant"):
    for d in os.listdir("/opt/ns/tenant"):
        p = os.path.join("/opt/ns/tenant", d)
        if os.path.isdir(p):
            tenant_dirs.append({"tenant": d, "count": dir_file_count(p), "mtime": fmtime(p)})
out["tenant"] = tenant_dirs
out["dirs"] = {
    "fastscan_count": dir_file_count("/opt/ns/cfg/fastscan_appliance"),
    "fastscan_mtime": fmtime("/opt/ns/cfg/fastscan_appliance"),
    "aisecurity_count": dir_file_count("/opt/ns/cfg/aisecurityservice_appliance"),
    "aisecurity_mtime": fmtime("/opt/ns/cfg/aisecurityservice_appliance"),
    "kmip_exists": os.path.isdir("/opt/ns/cfg/kmip"),
    "stray_2": os.path.exists("/opt/ns/2"),
}

# ---- kubectl: pods, events, describe -------------------------------------
pods_wide = run(KC + " get pods -n default -o wide 2>/dev/null")
out["pods"] = {"default_wide": pods_wide}
out["pods_events"] = run(KC + " get events -n default --sort-by=.lastTimestamp 2>/dev/null")
out["pods_all"] = run(KC + " get pods -A 2>/dev/null")

cfgagent_pod = callhome_pod = None
for line in pods_wide.splitlines():
    if "vpe-platform-cfgagent" in line and "Running" in line and not line.startswith("NAME"):
        cfgagent_pod = line.split()[0]
    if "callhome-agent" in line and "Running" in line and not line.startswith("NAME"):
        callhome_pod = line.split()[0]

def describe_grep(pod):
    return run(KC + " describe pod " + pod + " -n default 2>/dev/null | "
               "grep -E 'Start Time|Restart Count|State:|Last State|Reason:|Exit Code|Finished:|restartedAt|Image:'")

out["describe"] = {}
if cfgagent_pod:
    out["describe"]["cfgagent"] = describe_grep(cfgagent_pod)
if callhome_pod:
    out["describe"]["callhome"] = describe_grep(callhome_pod)

# ---- pod logs: structured greps (all matches, capped) --------------------
# Captured as LISTS so the local evaluator can filter to the current cycle by
# timestamp + serial. Raw tails are not shipped (keeps the JSON small and the
# logic explicit).
def podlog_text(pod, container, tail):
    return run(KC + " logs -n default " + pod + " -c " + container + " --tail=" + str(tail) + " 2>/dev/null")

out["podlogs"] = {}
if cfgagent_pod:
    txt = podlog_text(cfgagent_pod, "cfgagent", 600)
    out["podlogs"]["cfgagent_reconnect"] = _g(txt, r"Successfully reconnected to wss://")
    out["podlogs"]["cfgagent_echo"] = _g(txt, r"activity_watchdog")
    out["podlogs"]["cfgagent_gaierror"] = _g(txt, r"gaierror|Name or service not known")
    out["podlogs"]["cfgagent_disconnected"] = _g(txt, r"--Client Disconnected--")
    out["podlogs"]["cfgagent_status_connected"] = _g(txt, r"status': 'connected'|status.: 'connected'|\"status\": \"connected\"")
    out["podlogs"]["cfgagent_status_disconnected"] = _g(txt, r"status': 'disconnected'|status.: 'disconnected'|\"status\": \"disconnected\"")
if callhome_pod:
    wtxt = podlog_text(callhome_pod, "callhome-watcher", 200)
    out["podlogs"]["watcher_endpoint_found"] = _g(wtxt, r"Endpoint configuration found")
    out["podlogs"]["watcher_mtls"] = _g(wtxt, r"proxying with mTLS")
    out["podlogs"]["watcher_file_not_ready"] = _g(wtxt, r"File not ready")
    nxtxt = podlog_text(callhome_pod, "callhome-nginx", 200)
    out["podlogs"]["nginx_heartbeat_200"] = _g(nxtxt, r"POST /vpemanager/v1/heartbeat HTTP/1\.1\" 200")

# ---- file log signatures (ALL matches, capped; local filters by cycle) ----
N = "/opt/ns/log/nsclib.log"
H = "/opt/ns/log/heartbeat_sync.log"
D = "/opt/ns/log/diagnostic-agent.log"
C = "/opt/ns/log/cfgwatcher-cloudsync.log"
S = "/opt/ns/log/statsite.log"
out["logs"] = {
    "nsclib": {
        "enroll_start": grep_all(N, r"Starting VPE certificate enrollment for device vpe:"),
        "received_cert": grep_all(N, r"Received client certificate from management plane"),
        "enroll_success": grep_all(N, r"Certificate enrollment completed successfully"),
        "token_updated": grep_all(N, r"Registration token updated in"),
        "enroll_failed": grep_all(N, r"Certificate enrollment failed|Certificate request failed|Subject missing Country|failed to sign CSR|enrollment failed|CSR signing failed|HTTP [45]\d\d"),
        "reset_retain": grep_all(N, r"reset retain-network"),
        "deprovisioning": grep_all(N, r"Deprovisioning device"),
        "reset_done": grep_all(N, r"Reset all non network config: DONE"),
        "cannot_decode_cloudserial": grep_all(N, r"Cannot decode /opt/ns/cfg/cloudserial.json as JSON"),
    },
    "heartbeat_sync": {
        "server_requested_reset": grep_all(H, r"Server requested reset retain-network"),
        "reset_completed": grep_all(H, r"Reset retain-network completed successfully"),
        "reboot_issued": grep_all(H, r"Reboot issued"),
        "token_not_found": grep_all(H, r"Registration token file not found"),
        "requires_reset_true": grep_all(H, r'"requires_reset":\s*true'),
        "requires_reset_false": grep_all(H, r'"requires_reset":\s*false'),
    },
    "diagnostic_agent": {
        "connected_to_server": grep_all(D, r"Connected to server: callhome"),
        "serial_tenant": grep_all(D, r"Serial number:.*Tenant ID:"),
        "failed_read_callhome": grep_all(D, r"Failed to read callhome config"),
    },
    "cfgwatcher": {
        "copy_pushed": grep_all(C, r"Copying /opt/ns/cloud/pushed.json to /opt/ns/cfg/cloudconfig.json"),
    },
    "statsite": {
        "sent_metrics": grep_all(S, r"Sent metrics"),
        "serial_not_found": grep_all(S, r"Serial not found in cloud config"),
    },
}

# ---- DNS readiness --------------------------------------------------------
fqdn = None
ts = (st.get("tethering_status") or {})
if ts.get("tenant_url"):
    fqdn = "config-" + ts["tenant_url"]
if not fqdn:
    rt = out["cfg"].get("registration_token") or {}
    if rt.get("fqdn"):
        fqdn = "config-" + rt["fqdn"]
out["dns"] = run("nslookup " + fqdn + " 2>/dev/null | tail -6") if fqdn else ""

print(json.dumps(out))
'''


# ===========================================================================
# Local SSH runner
# ===========================================================================
def find_sshpass(override=None):
    """Locate the sshpass binary (not on PATH by default on macOS; lives in /opt/homebrew/bin)."""
    if override:
        return override
    p = shutil.which("sshpass")
    if p:
        return p
    for cand in ("/opt/homebrew/bin/sshpass", "/usr/local/bin/sshpass"):
        if os.path.exists(cand):
            return cand
    return None


def collect_from_vpe(ip, user, password, sshpass_path):
    """SSH to the VPE, pipe REMOTE_COLLECTOR to `sudo -n python3 -`, return parsed JSON dict."""
    remote_cmd = "sudo -n python3 -"
    ssh_cmd = [
        sshpass_path,
        "-p",
        password,
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "UserKnownHostsFile=/dev/null",
        "-o",
        "ConnectTimeout=" + SSH_CONNECT_TIMEOUT,
        "-o",
        "ServerAliveInterval=15",
        # Force password-only auth: without IdentitiesOnly/PubkeyAuthentication=no,
        # ssh offers every agent key first and the server disconnects with
        # "Too many authentication failures" before sshpass's password is tried
        # (matches ~/.login_ssh.sh which uses -o IdentitiesOnly=yes).
        "-o",
        "IdentitiesOnly=yes",
        "-o",
        "PreferredAuthentications=password",
        "-o",
        "PubkeyAuthentication=no",
        "%s@%s" % (user, ip),
        remote_cmd,
    ]
    try:
        proc = subprocess.run(
            ssh_cmd,
            input=REMOTE_COLLECTOR,
            capture_output=True,
            text=True,
            timeout=SSH_CMD_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        die("SSH collection timed out after %ds talking to %s." % (SSH_CMD_TIMEOUT, ip))
    except FileNotFoundError:
        die("sshpass not found at %r." % sshpass_path)

    if proc.returncode != 0:
        err = (proc.stderr or "").strip()
        die("SSH to %s failed (exit %s):\n%s" % (ip, proc.returncode, err[:800]))

    # The remote prints the JSON blob (possibly preceded by the SSH MOTN banner
    # "Authorized users only..."). Find the first '{' and parse from there.
    stdout = proc.stdout or ""
    idx = stdout.find("\n{")
    if idx >= 0:
        stdout = stdout[idx + 1 :]
    elif stdout.lstrip().startswith("{"):
        stdout = stdout.lstrip()
    else:
        die("Could not find JSON in remote output. First 500 chars:\n" + stdout[:500])
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as e:
        die(
            "Remote output was not valid JSON (%s). First 500 chars:\n%s"
            % (e, stdout[:500])
        )


def die(msg):
    sys.stderr.write("ERROR: " + msg + "\n")
    sys.exit(2)


# ===========================================================================
# Placeholder / context helpers
# ===========================================================================
def jwt_decode(jwt_str):
    """Decode a JWT payload (no signature verification) to a dict. Returns {} on failure."""
    if not jwt_str or jwt_str.count(".") != 2:
        return {}
    payload = jwt_str.split(".")[1]
    payload += "=" * (-len(payload) % 4)  # base64url pad
    try:
        import base64

        raw = base64.urlsafe_b64decode(payload.encode())
        return json.loads(raw)
    except Exception:
        return {}


def parse_openssl_dates(openssl_out):
    """Extract notBefore (datetime) from `openssl x509 -dates` output. None if absent."""
    if not openssl_out:
        return None
    m = re.search(r"notBefore=(.+GMT)", openssl_out)
    if not m:
        return None
    try:
        return _dt.datetime.strptime(m.group(1).strip(), "%b %d %H:%M:%S %Y %Z")
    except Exception:
        return None


def parse_openssl_subject(openssl_out):
    """Extract subject=... and pull CN and C (Country) from it."""
    if not openssl_out:
        return {}
    m = re.search(r"subject=(.*)", openssl_out)
    subj = m.group(1) if m else ""
    cn = ""
    cm = re.search(r"CN\s*=\s*([^,]+)", subj)
    if cm:
        cn = cm.group(1).strip()
    has_country = bool(re.search(r"\bC\s*=\s*[A-Z]{2}", subj))
    return {"subject": subj.strip(), "cn": cn, "has_country": has_country}


def build_context(d):
    """Fill the placeholder values (serial, fqdn, did, identifier, tid, license) from the box."""
    ts = (d.get("status") or {}).get("tethering_status") or {}
    cfg = d.get("cfg") or {}
    rt = cfg.get("registration_token") or {}
    cfgagent_conf = cfg.get("cfgagent_conf") or ""
    # license_key from cfgagent.conf (the `license_key: <value>` line)
    license_key = ""
    for line in cfgagent_conf.splitlines():
        m = re.match(r"\s*license_key:\s*(\S+)", line)
        if m:
            license_key = m.group(1)
            break
    # JWT in config.json system.registrationkey (decode for did/tid/fqdn/exp even if token file missing)
    jwt_payload = {}
    cj = cfg.get("config_json") or {}
    rk = (cj.get("system") or {}).get("registrationkey")
    if rk:
        jwt_payload = jwt_decode(rk)
    fqdn = rt.get("fqdn") or ts.get("tenant_url") or ""
    did = rt.get("device_id") or jwt_payload.get("did") or ""
    tid = rt.get("tenant_id") or str(jwt_payload.get("tid") or "")
    ctx = {
        "serial": ts.get("serial")
        or ((cfg.get("cloudserial") or {}).get("serial") or ""),
        "fqdn": fqdn,
        "configdist_host": "config-" + fqdn if fqdn else "",
        "did": did,
        "tid": tid,
        "identifier": ts.get("identifier") or "",
        "license_key": license_key,
        "jwt_exp": jwt_payload.get("exp"),
        "jwt_iat": jwt_payload.get("iat"),
        "build": (d.get("build") or {}).get("version") or "",
        "hostname": (d.get("build") or {}).get("hostname") or "ns-vpe",
    }
    # ---- cycle anchor ----
    # cycle_start = the moment the CURRENT tethering attempt ran ON THE APPLIANCE
    # (i.e. `set system registrationkey` + `save`). All current-cycle artifacts
    # (re-issued cert, re-pushed callhomeservice, success log lines) are
    # timestamped AT OR AFTER this. This anchor is what prevents stale logs/files
    # from a PRIOR tether cycle (on a box tethered+deprovisioned multiple times)
    # from being read as current-cycle success.
    #
    # Accuracy matters: registration_token.created_at is the JWT `iat` (server
    # token GENERATION time), which PRECEDES the appliance's `save` by seconds-to-
    # minutes. Using it as the anchor lets early-cycle "token not found" / "serial
    # not found" lines (between iat and the actual save) leak in as false
    # current-cycle failures. So we prefer, in order:
    #   1. nsclib "Starting VPE certificate enrollment for device vpe:<did>" ts
    #      (the exact save/enrollment moment on the box, for the current did)
    #   2. client.pem notBefore (cert issuance ~ save time)
    #   3. registration_token.created_at (iat — last resort, slightly early)
    age_min = None
    cycle_start_ts = None
    enroll_start_lines = ((d.get("logs") or {}).get("nsclib") or {}).get(
        "enroll_start"
    ) or []
    for ln in enroll_start_lines:
        if did and did in ln:
            ts = parse_line_ts(ln)
            if ts:
                # parse_line_ts returns a naive UTC datetime. Convert to epoch
                # explicitly as UTC (NOT .timestamp(), which treats naive as local
                # time and would shift the anchor by the host's TZ offset).
                cycle_start_ts = _naive_utc_to_epoch(ts)
                break
    if cycle_start_ts is None:
        nb = parse_openssl_dates(cfg.get("client_pem_openssl"))
        if nb:
            cycle_start_ts = _naive_utc_to_epoch(nb)
    if cycle_start_ts is None and rt.get("created_at"):
        try:
            cycle_start_ts = int(rt["created_at"])
        except Exception:
            pass
    if cycle_start_ts is None and jwt_payload.get("iat"):
        try:
            cycle_start_ts = int(jwt_payload["iat"])
        except Exception:
            pass
    if cycle_start_ts is not None:
        try:
            age_min = int(
                (
                    _dt.datetime.utcnow()
                    - _dt.datetime.utcfromtimestamp(cycle_start_ts)
                ).total_seconds()
                / 60
            )
        except Exception:
            pass
    ctx["age_min"] = age_min
    ctx["cycle_start_ts"] = cycle_start_ts
    ctx["cycle_start_dt"] = (
        _dt.datetime.utcfromtimestamp(cycle_start_ts) if cycle_start_ts else None
    )
    ctx["stale_filtered"] = (
        0  # incremented by current_cycle_lines when it drops stale matches
    )
    return ctx


# ---------------------------------------------------------------------------
# Cycle-aware filtering helpers
# ---------------------------------------------------------------------------
# Log-line timestamp formats seen on the VPE:
#   nsclib.log        : 2026-07-26:14:44:54,123 INFO ...
#   heartbeat_sync    : 2026-07-26 14:44:54,123 ...
#   diagnostic-agent  : 2026-07-26T14:56:50.345Z INFO ...
#   statsite          : 2026-07-26 14:10:08,075 INFO ...
#   cfgagent pod      : [2026-07-26 14:56:51,830] INFO ...   (also a tab-separated dup line)
#   callhome-watcher  : time=2026-07-26T14:56:40.079Z ...
#   callhome-nginx    : 10.42.0.1 - - [26/Jul/2026:14:57:00 +0000] "POST ...
_TS_PATTERNS = [
    (
        re.compile(r"(\d{4}-\d{2}-\d{2}):(\d{2}:\d{2}:\d{2})"),
        lambda m: "%s %s" % (m.group(1), m.group(2)),
        "%Y-%m-%d %H:%M:%S",
    ),
    (
        re.compile(r"(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2})"),
        lambda m: "%s %s" % (m.group(1), m.group(2)),
        "%Y-%m-%d %H:%M:%S",
    ),
    (
        re.compile(r"(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2})"),
        lambda m: m.group(1),
        "%Y-%m-%dT%H:%M:%S",
    ),
    (
        re.compile(r"\[(\d{2}/\w{3}/\d{4}:\d{2}:\d{2}:\d{2})"),
        lambda m: m.group(1),
        "%d/%b/%Y:%H:%M:%S",
    ),
    (
        re.compile(r"\[(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})"),
        lambda m: m.group(1),
        "%Y-%m-%d %H:%M:%S",
    ),
]


def parse_line_ts(line):
    """Extract a naive UTC datetime from a log line, or None if no timestamp found."""
    for rx, build, fmt in _TS_PATTERNS:
        m = rx.search(line)
        if m:
            try:
                return _dt.datetime.strptime(build(m), fmt)
            except Exception:
                continue
    return None


def _naive_utc_to_epoch(naive_utc):
    """Convert a naive datetime INTERPRETED AS UTC to a unix epoch integer.

    Using naive.timestamp() would interpret it in the HOST's local timezone and
    shift the result by the host TZ offset (e.g. +5:30 on an IST machine),
    silently corrupting the cycle anchor. calendar.timegm treats the tuple as UTC.
    """
    import calendar

    return int(calendar.timegm(naive_utc.timetuple()))


def current_cycle_lines(lines, ctx, require_serial=None, tolerance_s=120):
    """Filter `lines` (a list of log lines) to those belonging to the CURRENT cycle.

    A line belongs to the current cycle if its timestamp is >= cycle_start
    (minus a small tolerance for clock skew). If `require_serial` is set, the
    line must also contain that serial (for lines that carry one — e.g. cfgagent
    reconnect URL, statsite metric payload, diagnostic-agent Serial line). Lines
    with no parseable timestamp are KEPT (conservative — don't drop evidence
    just because we can't parse its timestamp). Lines from prior cycles are
    dropped and counted in ctx['stale_filtered'].
    """
    cstart = ctx.get("cycle_start_dt")
    out = []
    for ln in lines:
        if require_serial and require_serial not in ln:
            ctx["stale_filtered"] = ctx.get("stale_filtered", 0) + 1
            continue
        ts = parse_line_ts(ln)
        if ts is None or cstart is None:
            out.append(ln)  # unparseable timestamp or no cycle anchor -> keep
            continue
        if ts >= (cstart - _dt.timedelta(seconds=tolerance_s)):
            out.append(ln)
        else:
            ctx["stale_filtered"] = ctx.get("stale_filtered", 0) + 1
    return out


def file_is_current(mtime, ctx, tolerance_s=120):
    """True if `mtime` (epoch) is within the current cycle (>= cycle_start - tolerance).

    Returns True if no cycle anchor is set (S1/fresh: nothing to be current with)
    so callers can treat 'no cycle' as 'freshness not applicable'.
    """
    cstart = ctx.get("cycle_start_ts")
    if cstart is None or mtime is None:
        return True
    return mtime >= (cstart - tolerance_s)


# ===========================================================================
# Scenario classification
# ===========================================================================
def classify(d, ctx):
    """Return (scenario, reason, confidence). scenario in {S1,S2,S3,S4,FAILING,UNKNOWN}."""
    ts = (d.get("status") or {}).get("tethering_status") or {}
    reach = (d.get("status") or {}).get("reachability_status") or {}
    recon = (d.get("cfg") or {}).get("recon_status") or {}
    cfg = d.get("cfg") or {}
    remote = d.get("remote") or {}
    dirs = d.get("dirs") or {}
    logs = d.get("logs") or {}

    serial = ts.get("serial") or ""
    cfgagent_connected = bool(ts.get("cfgagent_connected"))
    callhome_reachable = bool(ts.get("callhome_reachable"))
    serial_files_match = bool(ts.get("serial_files_match"))
    configservice = bool(reach.get("configservice"))
    reset_requested = bool(recon.get("reset_requested"))
    has_token = bool(cfg.get("registration_token"))
    has_cert = bool(cfg.get("client_pem_exists"))
    cdpub = remote.get("configdist_core_pub") or ""
    configdist_placeholder = "CFG_SERVER_ENDPOINT" in cdpub
    callhomeservice_exists = bool(remote.get("callhomeservice_exists"))
    has_reset_markers = bool(dirs.get("kmip_exists")) or bool(dirs.get("stray_2"))
    reset_log = (
        bool(logs.get("nsclib", {}).get("reset_retain"))
        or bool(logs.get("heartbeat_sync", {}).get("reset_completed"))
        or bool(logs.get("heartbeat_sync", {}).get("server_requested_reset"))
    )
    attempted = has_token or has_cert or (cdpub.strip() and not configdist_placeholder)
    # A save can fail BEFORE persisting registration_token.json / client.pem /
    # rewriting configdist-core-pub (e.g. cert enrollment rejected at TCS), so
    # the artifact-based `attempted` is False even though the box is genuinely
    # failing (not fresh). The nsclib enrollment log is the reliable signal in
    # that case. Don't count it when there's reset evidence — a deprovisioned
    # box with stale enroll lines from a prior tether is handled by the S3
    # branch, not this one.
    _nclib = logs.get("nsclib") or {}
    enroll_log_attempt = (
        bool(_nclib.get("enroll_start")) or bool(_nclib.get("enroll_failed"))
    ) and not (reset_requested or reset_log or has_reset_markers)

    # 1) TETHERED — all four critical fields true.
    if serial and cfgagent_connected and callhome_reachable and serial_files_match:
        if reset_requested or has_reset_markers or reset_log:
            scen = "S4"
            reason = (
                "tethering_status all-true AND prior-reset evidence (recon.reset_requested=%s, reset-markers=%s, reset-log=%s)"
                % (reset_requested, has_reset_markers, reset_log)
            )
        else:
            scen = "S2"
            reason = "tethering_status all-true; recon.reset_requested=false; no reset markers/log"
        return scen, reason, "high"

    # 2) UNTETHERED — no serial and callhome not reachable.
    if not serial and not callhome_reachable:
        # S3: deprovisioned/stale — cfgagent reconnected to a PERSISTED real configdist fqdn,
        # stale token/cert present, AND there is reset evidence (deprovision ran).
        if (
            (
                configservice
                or (not configdist_placeholder and not callhomeservice_exists)
            )
            and (has_token or has_cert)
            and not configdist_placeholder
            and (reset_requested or reset_log or has_reset_markers)
        ):
            scen = "S3"
            reason = (
                "untethered (serial empty, callhome unreachable) but configservice=%s + stale token/cert + reset evidence (reset_requested=%s, reset-log=%s, markers=%s)"
                % (configservice, reset_requested, reset_log, has_reset_markers)
            )
            return scen, reason, "high"
        # FAILING/IN-PROGRESS: an enrollment was attempted (token/cert present, or real configdist
        # fqdn, or nsclib enrollment log shows a start/failure) but tethering hasn't completed and
        # there's NO reset evidence (so not a deprovision).
        if (attempted or enroll_log_attempt) and not (
            reset_requested or reset_log or has_reset_markers
        ):
            age = ctx.get("age_min")
            via_log = (not attempted) and enroll_log_attempt
            attempt_desc = (
                "nsclib enrollment log shows an attempt"
                if via_log
                else ("token=%s cert=%s" % (has_token, has_cert))
            )
            if age is not None and age < TETHER_GRACE_MIN:
                tag = "IN-PROGRESS"
                reason = (
                    "enrollment attempted (%s) but not all phases complete; age %d min (<%d-min grace)"
                    % (attempt_desc, age, TETHER_GRACE_MIN)
                )
            elif age is not None:
                tag = "FAILING"
                reason = (
                    "enrollment attempted (%s) but tethering incomplete after %d min (>= %d-min expectation)"
                    % (attempt_desc, age, TETHER_GRACE_MIN)
                )
            else:
                tag = "FAILING"
                reason = (
                    "enrollment attempted (%s) but tethering incomplete; age unknown"
                    % attempt_desc
                )
            return tag, reason, "medium"
        # S1: fresh — nothing attempted, placeholder configdist endpoint, no token/cert.
        if not attempted and configdist_placeholder and not has_token and not has_cert:
            scen = "S1"
            reason = (
                "untethered, no enrollment attempt (no token/cert), configdist-core-pub=CFG_SERVER_ENDPOINT placeholder, configservice=%s"
                % configservice
            )
            return scen, reason, "high"
        # Doesn't cleanly fit S1/S3/failing.
        return (
            "UNKNOWN",
            "untethered state doesn't match any known untethered state (fresh/deprovisioned/failing) (token=%s cert=%s placeholder=%s configservice=%s reset=%s/%s)"
            % (
                has_token,
                has_cert,
                configdist_placeholder,
                configservice,
                reset_requested,
                reset_log,
            ),
            "low",
        )

    # 3) PARTIAL — some critical fields true, some false (e.g. serial present but callhome not up).
    age = ctx.get("age_min")
    if age is not None and age < TETHER_GRACE_MIN:
        tag = "IN-PROGRESS"
    else:
        tag = "FAILING"
    reason = (
        "partial tethering state (serial=%r cfgagent_connected=%s callhome_reachable=%s serial_files_match=%s); age %s min"
        % (serial[:16], cfgagent_connected, callhome_reachable, serial_files_match, age)
    )
    return tag, reason, "medium"


# ===========================================================================
# Mark constants
# ===========================================================================
TICK = "tick"
CROSS = "cross"
WARN = "warn"
NA = "na"

# Scenario -> human-readable name (shared by the text renderer and the JSON
# report builder).
SCENARIO_NAMES = {
    "S1": "S1 — Fresh, never tethered",
    "S2": "S2 — First tethering (tethered, no prior reset)",
    "S3": "S3 — Deprovisioned (stale config from prior tether)",
    "S4": "S4 — Re-tethered (tethered again after deprovision)",
    "FAILING": "TETHERING INCOMPLETE / FAILING (enrolled but not all phases passed)",
    "IN-PROGRESS": "TETHERING IN PROGRESS (within %d-min grace)" % TETHER_GRACE_MIN,
    "UNKNOWN": "UNKNOWN — does not match any modeled scenario (manual review)",
}


# ===========================================================================
# Check helpers (each returns (mark, reason) given data d and context ctx)
# ===========================================================================
def _pg(d, key):
    """Return a pod-log structured grep list (captured remotely as a list of lines)."""
    v = (d.get("podlogs") or {}).get(key)
    return v if isinstance(v, list) else []


def _lg(d, *path):
    """Return a file-log grep list nested under d['logs'][path...]."""
    cur = d.get("logs") or {}
    for p in path:
        cur = cur.get(p) if isinstance(cur, dict) else None
    return cur if isinstance(cur, list) else []


def _has(lines):
    return bool(lines) if isinstance(lines, list) else bool(lines)


def _last(lines):
    if isinstance(lines, list):
        return lines[-1] if lines else ""
    return lines or ""


# ---- PRE-FLIGHT ----
def chk_pre_build(d, ctx):
    v = ctx.get("build")
    return (TICK if v else CROSS), "build %s on %s" % (
        v or "UNKNOWN",
        ctx.get("hostname"),
    )


def chk_pre_pods(d, ctx):
    ts = (d.get("status") or {}).get("tethering_status") or {}
    pods = (d.get("pods") or {}).get("default_wide") or ""
    required = ["vpe-platform-cfgagent", "callhome-agent"]
    running = all(p in pods for p in required)
    vault = "vault-agent-injector" in pods
    ok = bool(ts.get("required_pods_running")) and running
    return (
        TICK if ok else CROSS
    ), "required_pods_running=%s; cfgagent+callhome Running=%s; vault=%s" % (
        ts.get("required_pods_running"),
        running,
        vault,
    )


# ---- STAGE 0: registration key + cert enrollment ----
def chk_0a_token(d, ctx):
    rt = (d.get("cfg") or {}).get("registration_token") or {}
    if not rt:
        return CROSS, "registration_token.json MISSING"
    need = ["tenant_id", "device_id", "fqdn", "license_key"]
    missing = [k for k in need if not rt.get(k)]
    expired = False
    if rt.get("expired_at"):
        try:
            expired = (
                _dt.datetime.utcfromtimestamp(rt["expired_at"]) < _dt.datetime.utcnow()
            )
        except Exception:
            pass
    if missing:
        return (
            CROSS,
            "registration_token.json present but missing fields: %s"
            % ",".join(missing),
        )
    if expired:
        return CROSS, "registration_token.json EXPIRED (expired_at %s)" % rt.get(
            "expired_at"
        )
    return TICK, "registration_token.json valid (tenant %s, did %s, fqdn %s)" % (
        rt.get("tenant_id"),
        rt.get("device_id"),
        rt.get("fqdn"),
    )


def chk_0b_system(d, ctx):
    cfg = d.get("cfg") or {}
    sj = cfg.get("system_json") or {}
    lk = (sj.get("licensekey") or "") if isinstance(sj, dict) else ""
    # S3 empties system.json to "{}" -> no licensekey -> cross for tethered target
    if not (lk and lk != "ABCD"):
        return CROSS, "system.json licensekey not set (system.json=%r)" % (
            sj if sj else "MISSING"
        )
    # Cycle freshness: system.json must have been (re)written THIS cycle. A
    # licensekey from a prior cycle (mtime precedes cycle_start) is stale.
    if not file_is_current(cfg.get("system_json_mtime"), ctx):
        return (
            CROSS,
            "system.json licensekey=%s... but file is STALE (mtime precedes current enrollment)"
            % lk[:24],
        )
    return TICK, "system.json licensekey=%s... (current-cycle)" % lk[:24]


def chk_0c_cfgagent_conf(d, ctx):
    cfg = d.get("cfg") or {}
    lk = ctx.get("license_key") or ""
    if not (lk and lk != "ABCD"):
        return (
            CROSS,
            "cfgagent.conf license_key=%r (placeholder 'ABCD' = not written)" % lk,
        )
    if not file_is_current(cfg.get("cfgagent_conf_mtime"), ctx):
        return (
            CROSS,
            "cfgagent.conf license_key real but file is STALE (mtime precedes current enrollment)",
        )
    return (
        TICK,
        "cfgagent.conf license_key=%s... (current-cycle, not placeholder ABCD)"
        % lk[:24],
    )


def chk_0d_config_jwt(d, ctx):
    cj = (d.get("cfg") or {}).get("config_json") or {}
    rk = (cj.get("system") or {}).get("registrationkey") or ""
    if not rk:
        return CROSS, "config.json system.registrationkey MISSING (JWT not stored)"
    p = jwt_decode(rk)
    if not p:
        return CROSS, "config.json registrationkey present but JWT undecodable"
    exp = p.get("exp")
    expired = False
    if exp:
        try:
            expired = _dt.datetime.utcfromtimestamp(exp) < _dt.datetime.utcnow()
        except Exception:
            pass
    if expired:
        return CROSS, "JWT EXPIRED (exp %s)" % exp
    # Cross-check the JWT did against the current registration_token's device_id.
    # If they differ, config.json still holds a PRIOR cycle's JWT (stale).
    jwt_did = p.get("did") or ""
    rt_did = ((d.get("cfg") or {}).get("registration_token") or {}).get(
        "device_id"
    ) or ""
    if rt_did and jwt_did and jwt_did != rt_did:
        return (
            CROSS,
            "JWT did=%s != registration_token.device_id=%s (config.json JWT is STALE from a prior cycle)"
            % (jwt_did, rt_did),
        )
    meta = p.get("metadata")
    meta_fqdn = None
    if isinstance(meta, str):
        try:
            meta_fqdn = (json.loads(meta) or {}).get("fqdn")
        except Exception:
            pass
    elif isinstance(meta, dict):
        meta_fqdn = meta.get("fqdn")
    return (
        TICK,
        "JWT present (did=%s, tid=%s, fqdn=%s, exp=%s; did matches current token)"
        % (jwt_did, p.get("tid"), meta_fqdn or ctx.get("fqdn"), exp),
    )


def chk_0e_enroll_seq(d, ctx):
    """Stage 0: certificate enrollment ran AND succeeded in the CURRENT cycle.

    Cycle-aware: we only count nsclib enrollment lines whose timestamp is >= the
    current cycle_start AND (for enroll_start) whose device id == the current
    token's did. A box tethered+deprovisioned multiple times has enrollment
    success lines from every prior cycle; without this filter a prior-cycle
    success would masquerade as the current cycle's success.
    """
    n = (d.get("logs") or {}).get("nsclib") or {}
    did = ctx.get("did") or ""
    # enroll_start lines carry a timestamp + the device id; filter to current cycle + current did.
    start_lines = current_cycle_lines(n.get("enroll_start", []), ctx)
    if did:
        start_lines = [ln for ln in start_lines if did in ln]
    recv_lines = current_cycle_lines(n.get("received_cert", []), ctx)
    # enroll_success appears inside a "display" JSON line that may not carry its
    # own timestamp; keep it as corroborating evidence (not cycle-gated) but only
    # trust it as a SUCCESS if a current-cycle enroll_start + received_cert exist.
    succ_present = _has(n.get("enroll_success"))
    # Failure lines (HTTP 500 / "failed to sign CSR" / "Subject missing Country" /
    # "Certificate request failed" / "enrollment failed"). These are the `save`
    # failing mid-enrollment. Surface the ACTUAL failure message to the user.
    fail_lines = current_cycle_lines(n.get("enroll_failed", []), ctx)

    # 1) Current-cycle SUCCESS wins (a prior-cycle stale failure line must not
    #    override a current success).
    if start_lines and recv_lines:
        extra = (
            ' + "Certificate enrollment completed successfully"'
            if succ_present
            else " (enrollment-success display line not seen)"
        )
        return (
            TICK,
            'nsclib (current cycle): "Starting VPE certificate enrollment for device %s" -> "Received client certificate from management plane"%s'
            % (did or "vpe:<did>", extra),
        )
    # 2) No current success + a failure line -> the save/enrollment FAILED.
    #    Surface the real error so the user sees e.g. "HTTP 500: failed to sign CSR".
    if fail_lines:
        # Prefer a timestamped (current-cycle) failure line; fall back to the last
        # captured (may be a display line without its own timestamp).
        ts_fails = [ln for ln in fail_lines if parse_line_ts(ln) is not None]
        msg = _last(ts_fails) if ts_fails else _last(fail_lines)
        # Extract the human-readable failure text from the line (strip nsclib log prefix).
        m = re.search(
            r"(Certificate enrollment failed.*|Certificate request failed.*|Subject missing Country.*|failed to sign CSR.*|enrollment failed.*|HTTP [45]\d\d.*)",
            msg,
        )
        human = m.group(1)[:200] if m else msg[:200]
        return CROSS, "current-cycle enrollment FAILED: %s" % human
    # 3) No success, no explicit failure -> enrollment didn't complete / didn't run.
    miss = []
    if not start_lines:
        miss.append(
            '"Starting VPE certificate enrollment for device %s" in current cycle'
            % (did or "vpe:<did>")
        )
    if not recv_lines:
        miss.append(
            '"Received client certificate from management plane" in current cycle'
        )
    return (
        CROSS,
        "current-cycle enrollment not confirmed; missing in nsclib: %s"
        % ", ".join(miss),
    )


def chk_0f_cert(d, ctx):
    cfg = d.get("cfg") or {}
    if not cfg.get("client_pem_exists"):
        return CROSS, "client.pem MISSING (enrollment did not produce a client cert)"
    sub = parse_openssl_subject(cfg.get("client_pem_openssl"))
    nb = parse_openssl_dates(cfg.get("client_pem_openssl"))
    not_after_ok = True
    m = re.search(r"notAfter=(.+GMT)", cfg.get("client_pem_openssl") or "")
    if m:
        try:
            not_after_ok = (
                _dt.datetime.strptime(m.group(1).strip(), "%b %d %H:%M:%S %Y %Z")
                > _dt.datetime.utcnow()
            )
        except Exception:
            pass
    key_ok = bool(cfg.get("client_key_exists"))
    ca_ok = bool(cfg.get("issuer_ca_exists"))
    did = ctx.get("did") or ""
    cn_matches = bool(did) and (did in sub.get("cn", ""))
    country_ok = sub.get("has_country")
    # Cycle freshness: the cert must have been (re)issued THIS cycle. client.pem
    # notBefore precedes cycle_start => the cert is a stale leftover from a prior
    # tether (deprovision didn't remove it AND re-enrollment didn't re-issue).
    nb_current = True
    if nb is not None and ctx.get("cycle_start_dt") is not None:
        nb_current = nb >= (ctx["cycle_start_dt"] - _dt.timedelta(seconds=120))
    problems = []
    if not cn_matches:
        problems.append("CN=%r != expected vpe:<did>=%r" % (sub.get("cn"), did))
    if not country_ok:
        problems.append(
            "Country (C=) missing in subject — pre-ENG-1007978 build would hit TCS HTTP 500"
        )
    if not not_after_ok:
        problems.append("cert EXPIRED")
    if not nb_current:
        problems.append(
            "cert is STALE (notBefore %s precedes current enrollment %s — not re-issued this cycle)"
            % (
                nb.strftime("%Y-%m-%d %H:%M") if nb else "?",
                ctx["cycle_start_dt"].strftime("%Y-%m-%d %H:%M"),
            )
        )
    if not key_ok:
        problems.append("client.key MISSING")
    if not ca_ok:
        problems.append("issuer_ca.pem MISSING")
    if problems:
        return CROSS, "client.pem issues: " + "; ".join(problems)
    return (
        TICK,
        "client.pem CN=%s, C=US present, valid, re-issued this cycle; client.key + issuer_ca.pem present"
        % sub.get("cn"),
    )


# ---- STAGE 1: cfgagent WebSocket -> configdist ----
def chk_1a_endpoint(d, ctx):
    cdpub = (d.get("remote") or {}).get("configdist_core_pub") or ""
    host = ctx.get("configdist_host")
    if "CFG_SERVER_ENDPOINT" in cdpub:
        return (
            CROSS,
            "configdist-core-pub still placeholder 'CFG_SERVER_ENDPOINT' (registration key not applied / endpoint not rewritten)",
        )
    if host and host in cdpub:
        return TICK, "configdist-core-pub host = %s (matches tenant fqdn)" % host
    # real fqdn present but doesn't match ctx (e.g. stale from a prior tenant) — still a real endpoint
    m = re.search(r'"host"\s*:\s*"([^"]+)"', cdpub)
    h = m.group(1) if m else cdpub.strip()
    if h:
        return TICK, "configdist-core-pub host = %s (real; expected %s)" % (
            h,
            host or "?",
        )
    return CROSS, "configdist-core-pub unreadable: %r" % cdpub[:80]


def chk_1b_ws_connected(d, ctx):
    """Stage 1: cfgagent established the WebSocket to configdist IN THE CURRENT CYCLE.

    Cycle-aware: only a `Successfully reconnected to wss://config-<fqdn>:443/...`
    line timestamped >= cycle_start counts. If the current serial is known, the
    line must also carry it (a reconnect line with an OLD serial is from a prior
    cycle). A connected cfgagent with serial= empty is a stage-2 stall, not a
    stage-1 failure, so we tick stage 1 if a current-cycle reconnect exists even
    with an empty serial.
    """
    host = ctx.get("configdist_host") or "config-<fqdn>"
    serial = ctx.get("serial") or ""
    reconnects = _pg(d, "cfgagent_reconnect")
    # Filter to current cycle; if we know the current serial, also require it to
    # be the serial in the URL (drops prior-cycle reconnects with old serials).
    cur = current_cycle_lines(
        reconnects, ctx, require_serial=(serial if serial else None)
    )
    # Even with no serial known yet (stage 2 not done), a current-cycle reconnect
    # to the real host means stage 1 itself succeeded.
    if not cur and serial:
        # retry without the serial requirement (stage 2 may not have assigned one yet)
        cur = current_cycle_lines(reconnects, ctx)
    cur = [ln for ln in cur if ("wss://%s:443/configdist/sf" % host) in ln]
    if cur:
        m = re.search(r"serial=([^&\" ]+)", cur[-1])
        surl = m.group(1) if m else ""
        if surl:
            return (
                TICK,
                'cfgagent (current cycle): "Successfully reconnected to wss://%s:443/configdist/sf?...&serial=%s&identifier=%s..."'
                % (host, surl, ctx.get("identifier") or ""),
            )
        return (
            TICK,
            'cfgagent (current cycle): "Successfully reconnected to wss://%s:443/..." (connected; serial= empty in URL — serial assignment is stage 2)'
            % host,
        )
    if _pg(d, "cfgagent_gaierror"):
        return (
            CROSS,
            "cfgagent cannot resolve configdist host (socket.gaierror in current tail) — endpoint is CFG_SERVER_ENDPOINT placeholder or DNS is broken",
        )
    return (
        CROSS,
        'cfgagent log has no CURRENT-CYCLE "Successfully reconnected to wss://%s:443/..." (only stale/prior-cycle reconnects, or none)'
        % host,
    )


def chk_1c_cfgagent_connected(d, ctx):
    ts = (d.get("status") or {}).get("tethering_status") or {}
    reach = (d.get("status") or {}).get("reachability_status") or {}
    cur = ((d.get("cfgagent_state") or {}).get("current") or {}).get("status")
    ok = (
        bool(ts.get("cfgagent_connected"))
        and bool(reach.get("configservice"))
        and cur == "connected"
    )
    return (
        TICK if ok else CROSS
    ), "tethering_status.cfgagent_connected=%s; reachability.configservice=%s; cfgagent_current_status=%s" % (
        ts.get("cfgagent_connected"),
        reach.get("configservice"),
        cur,
    )


# ---- STAGE 2: configdist pushed config + serial (all current-cycle) ----
def chk_2a_callhomeservice(d, ctx):
    r = d.get("remote") or {}
    if not r.get("callhomeservice_exists"):
        return (
            CROSS,
            "remote/callhomeservice ABSENT — configdist has NOT pushed config this cycle (stage 2 not reached)",
        )
    # Cycle freshness: callhomeservice is (re)created by configdist each tether.
    # An mtime preceding cycle_start means the file is a stale leftover from a
    # prior cycle that deprovision failed to remove — NOT current-cycle push.
    if not file_is_current(r.get("callhomeservice_mtime"), ctx):
        return (
            CROSS,
            "remote/callhomeservice exists but is STALE (mtime precedes current enrollment) — not pushed this cycle",
        )
    m = re.search(r'"host"\s*:\s*"([^"]+)"', r.get("callhomeservice") or "")
    return TICK, "remote/callhomeservice created this cycle (host=%s)" % (
        m.group(1) if m else "?"
    )


def chk_2b_serial(d, ctx):
    ts = (d.get("status") or {}).get("tethering_status") or {}
    cfg = d.get("cfg") or {}
    cloud = d.get("cloud") or {}
    cfg_serial = (cfg.get("cloudserial") or {}).get("serial") or ""
    cloud_serial = (cloud.get("serial") or {}).get("serial") or ""
    serial = ctx.get("serial") or ""
    match = (
        bool(ts.get("serial_files_match"))
        and cfg_serial
        and cloud_serial
        and cfg_serial == cloud_serial
    )
    if not (serial and match):
        if not serial:
            return (
                CROSS,
                "serial empty — configdist has not assigned one this cycle (cloudserial=%r, cloud/serial=%r)"
                % (cfg_serial, cloud_serial),
            )
        return (
            CROSS,
            "serial present (%s) but files mismatch (cfg=%r, cloud=%r, match=%s)"
            % (serial, cfg_serial, cloud_serial, ts.get("serial_files_match")),
        )
    # Cycle freshness on both serial files.
    stale = []
    if not file_is_current(cfg.get("cloudserial_mtime"), ctx):
        stale.append("cloudserial.json")
    if not file_is_current(cloud.get("serial_mtime"), ctx):
        stale.append("cloud/serial")
    if stale:
        return (
            CROSS,
            "serial=%s matches but %s STALE (mtime precedes current enrollment) — prior-cycle serial"
            % (serial, " + ".join(stale)),
        )
    return (
        TICK,
        "serial=%s; cloudserial.json==cloud/serial; serial_files_match=%s; both written this cycle"
        % (serial, ts.get("serial_files_match")),
    )


def chk_2c_cloudconfig(d, ctx):
    """Stage 2c: cloudconfig.json / pushed.json populated.

    `pushed.json` carries the tenant's DNS-intercepted-domains config (4.5 MB on
    stg01). Some tenants/stacks — notably prod tenants that don't use DNS
    interception — push little/no pushed.json, so an empty pushed.json is NOT by
    itself a stage-2 failure. We only CROSS when the rest of stage 2 is ALSO
    empty (genuine configdist push stall); if sfconfig/serial/tenant config all
    populated, an empty pushed.json is downgraded to WARN (verify DNS config is
    expected for this tenant).
    """
    cfg = d.get("cfg") or {}
    cloud = d.get("cloud") or {}
    cfg_sz = cfg.get("cloudconfig_size") or 0
    pushed_sz = cloud.get("pushed_size") or 0
    if cfg_sz > 1000 and pushed_sz > 1000:
        stale = []
        if not file_is_current(cfg.get("cloudconfig_mtime"), ctx):
            stale.append("cloudconfig.json")
        if not file_is_current(cloud.get("pushed_mtime"), ctx):
            stale.append("pushed.json")
        if stale:
            return (
                CROSS,
                "cloud config populated but %s STALE (mtime precedes current enrollment)"
                % " + ".join(stale),
            )
        return TICK, "cloudconfig.json %dB; pushed.json %dB (populated this cycle)" % (
            cfg_sz,
            pushed_sz,
        )
    # pushed.json empty — is the rest of stage 2 healthy?
    sf_ok = (cloud.get("sfconfig_size") or 0) > 1000
    serial_ok = bool(
        (cloud.get("serial") or {}).get("serial")
        or (cfg.get("cloudserial") or {}).get("serial")
    )
    tenant_ok = any((t.get("count", 0) > 5) for t in (d.get("tenant") or []))
    if sf_ok and serial_ok and tenant_ok:
        return (
            WARN,
            "cloudconfig.json %dB; pushed.json %dB (empty) — other stage-2 config (sfconfig/serial/tenant) IS populated, so stage 2 succeeded; empty pushed.json is likely expected for tenants without DNS-intercepted config (e.g. prod). Verify if DNS interception is expected for this tenant."
            % (cfg_sz, pushed_sz),
        )
    return (
        CROSS,
        "cloudconfig.json %dB; pushed.json %dB (empty) AND other stage-2 config also missing — configdist push stall"
        % (cfg_sz, pushed_sz),
    )


def chk_2d_sfconfig_resttoken(d, ctx):
    cloud = d.get("cloud") or {}
    sf_sz = cloud.get("sfconfig_size") or 0
    rt = cloud.get("rest_api_token") or {}
    tok = (rt.get("rest-token") or "") if isinstance(rt, dict) else ""
    if not (sf_sz > 1000 and tok):
        return CROSS, "sfconfig.json %dB; rest_api_token=%r (not populated)" % (
            sf_sz,
            tok,
        )
    stale = []
    if not file_is_current(cloud.get("sfconfig_mtime"), ctx):
        stale.append("sfconfig.json")
    if not file_is_current(cloud.get("rest_api_token_mtime"), ctx):
        stale.append("rest_api_token.json")
    if stale:
        return (
            CROSS,
            "sfconfig/rest_api_token populated but %s STALE (mtime precedes current enrollment)"
            % " + ".join(stale),
        )
    return (
        TICK,
        "sfconfig.json %dB; rest_api_token present (token=%s...); written this cycle"
        % (sf_sz, tok[:12]),
    )


def chk_2e_tenant(d, ctx):
    tenants = d.get("tenant") or []
    if not tenants:
        return (
            CROSS,
            "/opt/ns/tenant/<id>/ ABSENT — tenant config not pushed this cycle",
        )
    t = tenants[0]
    if t.get("count", 0) <= 5:
        return (
            CROSS,
            "/opt/ns/tenant/%s/ present but only %d files (under-populated)"
            % (t.get("tenant"), t.get("count")),
        )
    if not file_is_current(t.get("mtime"), ctx):
        return (
            CROSS,
            "/opt/ns/tenant/%s/ populated but dir STALE (mtime precedes current enrollment) — prior-cycle tenant config"
            % t.get("tenant"),
        )
    return TICK, "/opt/ns/tenant/%s/ populated this cycle (%d files)" % (
        t.get("tenant"),
        t.get("count"),
    )


def chk_2f_watchdog_cfgwatcher(d, ctx):
    """Stage 2 corroboration: cfgagent watchdog ECHO carries the CURRENT serial,
    and cfgwatcher copied pushed.json -> cloudconfig.json, both in the current cycle.

    The watchdog ECHO-with-serial is the strong signal (cfgagent is connected and
    has been provisioned with a serial). The cfgwatcher 'Copying pushed.json' line
    only appears if configdist pushed a non-empty pushed.json — which some tenants
    (e.g. prod without DNS-intercepted config) don't. So: ECHO-with-serial present
    + copy missing -> WARN (not cross); ECHO-with-serial ALSO missing -> cross.
    """
    serial = ctx.get("serial") or ""
    echo_lines = _pg(d, "cfgagent_echo")
    echo_ok = False
    if serial:
        cur_echo = current_cycle_lines(echo_lines, ctx, require_serial=serial)
        echo_ok = any("activity_watchdog" in ln for ln in cur_echo)
    copy_lines = current_cycle_lines(_lg(d, "cfgwatcher", "copy_pushed"), ctx)
    copy_ok = bool(copy_lines)
    if echo_ok and copy_ok:
        return (
            TICK,
            'cfgagent watchdog ECHO (current cycle) serial=%s; cfgwatcher "Copying pushed.json -> cloudconfig.json"'
            % serial,
        )
    if echo_ok and not copy_ok:
        return (
            WARN,
            'cfgagent watchdog ECHO with serial=%s present (cfgagent connected+provisioned) but cfgwatcher "Copying pushed.json" not seen this cycle — pushed.json likely not pushed for this tenant (expected for tenants without DNS-intercepted config, e.g. prod)'
            % serial,
        )
    miss = ["watchdog ECHO with current serial=%s" % (serial or "(none yet)")]
    if not copy_ok:
        miss.append('cfgwatcher "Copying pushed.json" in current cycle')
    return CROSS, "missing: %s" % ", ".join(miss)


# ---- STAGE 2 (cross-stack): endpoint/tenant/content consistency ----
# These catch a re-tether to a DIFFERENT tenant/stack where stale files from the
# prior tenant persist (the same-tenant S4 re-tether doesn't exercise this).
def chk_2g_remote_endpoints_match(d, ctx):
    """Every remote/* endpoint host must match the CURRENT tenant fqdn.

    On a same-tenant re-tether all endpoints match (they're re-pushed or persist
    with the same fqdn). On a cross-tenant re-tether, any endpoint file NOT
    re-pushed by the new configdist keeps the OLD tenant's host -> mismatch ->
    the box would send logs/ssh-tunnel/UI to the wrong stack. This is the check
    that catches the 'configdist-core-pub not rewritten' / 'mixed endpoints'
    latent defects on a cross-stack move.
    """
    fqdn = ctx.get("fqdn") or ""
    if not fqdn:
        return (
            CROSS,
            "no current tenant fqdn to compare remote endpoints against (JWT/token fqdn missing)",
        )
    files = (d.get("remote") or {}).get("files") or {}
    if not files:
        return CROSS, "remote/ has no endpoint files (configdist hasn't pushed any)"
    mismatches = []
    for name, content in sorted(files.items()):
        m = re.search(r'"host"\s*:\s*"([^"]+)"', content or "")
        if m:
            host = m.group(1)
            if fqdn not in host:
                mismatches.append("%s=%s" % (name, host))
    if mismatches:
        return (
            CROSS,
            "remote endpoint(s) don't match current tenant fqdn '%s': %s — STALE from a prior tenant/stack (not re-pushed this tether)"
            % (fqdn, "; ".join(mismatches)),
        )
    return TICK, "all %d remote/* endpoint hosts match current tenant fqdn %s" % (
        len(files),
        fqdn,
    )


def chk_2h_tenant_dir_current(d, ctx):
    """/opt/ns/tenant/ must contain ONLY the current tenant's dir.

    A same-tenant re-tether has one dir (the current tenant, re-populated). A
    cross-tenant re-tether without a prior deprovision may leave the OLD tenant's
    dir in place -> two tenant config dirs on disk -> mixed/wrong tenant config.
    """
    tid = ctx.get("tid") or ""
    tenants = d.get("tenant") or []
    if not tenants:
        return (
            CROSS,
            "/opt/ns/tenant/<id>/ ABSENT — tenant config not pushed this cycle",
        )
    if not tid:
        return (
            TICK,
            "/opt/ns/tenant/ has %d dir(s) (current tid unknown from token — not strictly checked)"
            % len(tenants),
        )
    others = [t for t in tenants if str(t.get("tenant")) != str(tid)]
    if others:
        return (
            CROSS,
            "leftover tenant dir(s) from a prior tenant/stack: %s — expected only %s (deprovision wasn't run before this tether)"
            % (", ".join(str(t.get("tenant")) for t in others), tid),
        )
    return TICK, "/opt/ns/tenant/ contains only the current tenant %s" % tid


def chk_2i_content_refreshed(d, ctx):
    """fastscan/aisecurity appliance content refresh status (informational).

    S4 proved these content dirs are NOT re-pushed on a same-tenant re-tether
    (idempotent). That's fine for same-tenant. But on a CROSS-tenant re-tether,
    stale content means the box runs the OLD tenant's AIS patterns / TSS
    prefilters. This is a WARN (not cross) because same-tenant staleness is
    expected; the human must judge based on whether the tenant changed.
    """
    dirs = d.get("dirs") or {}
    fs_mt = dirs.get("fastscan_mtime")
    ais_mt = dirs.get("aisecurity_mtime")
    cstart = ctx.get("cycle_start_ts")
    if cstart is None:
        return (
            WARN,
            "content dir refresh not checked (no current-cycle anchor — fresh/unknown)",
        )
    stale = []
    if (
        dirs.get("fastscan_count", 0) > 0
        and fs_mt is not None
        and fs_mt < (cstart - 120)
    ):
        stale.append("fastscan_appliance (mtime precedes current enrollment)")
    if (
        dirs.get("aisecurity_count", 0) > 0
        and ais_mt is not None
        and ais_mt < (cstart - 120)
    ):
        stale.append("aisecurityservice_appliance (mtime precedes current enrollment)")
    if stale:
        return (
            WARN,
            "content dir(s) NOT re-pushed this cycle: %s — OK for same-tenant re-tether; for CROSS-tenant this *could* mean stale content from the old tenant. NOTE: if AIS/TSS service templates are not enabled on this tenant, this content is inception default (pushed once at first tether, never refreshed because the services are unused) and staleness is expected — not a bug. Only a concern if AIS/TSS is enabled and the content differs per tenant."
            % "; ".join(stale),
        )
    return TICK, "content dirs current-cycle (refreshed this tether, or empty)"


# ---- STAGE 3: callhome-agent reached callhome (current cycle) ----
def chk_3a_watcher(d, ctx):
    found = current_cycle_lines(_pg(d, "watcher_endpoint_found"), ctx)
    mtls = current_cycle_lines(_pg(d, "watcher_mtls"), ctx)
    if found and mtls:
        return (
            TICK,
            'callhome-watcher (current cycle): "Endpoint configuration found" + "Certificates found, proxying with mTLS"',
        )
    not_ready = current_cycle_lines(_pg(d, "watcher_file_not_ready"), ctx)
    if not_ready:
        return (
            CROSS,
            'callhome-watcher still (current cycle): "File not ready ... callhomeservice ... not found" (endpoint file not created)',
        )
    return (
        CROSS,
        'callhome-watcher: no CURRENT-CYCLE "Endpoint configuration found" (only stale/prior-cycle, or none)',
    )


def chk_3b_nginx_heartbeat(d, ctx):
    beats = current_cycle_lines(_pg(d, "nginx_heartbeat_200"), ctx)
    if beats:
        return TICK, "callhome-nginx (current cycle): %d heartbeat 200 responses" % len(
            beats
        )
    return (
        CROSS,
        "callhome-nginx: no CURRENT-CYCLE heartbeat 200 responses (only stale/prior-cycle, or none)",
    )


def chk_3c_diag(d, ctx):
    """Stage 3c: diagnostic-agent connected to callhome with the CURRENT serial.

    Two content signals, both cycle-filtered:
      - strong : "Serial number: <serial>, Tenant ID: <tid>" (carries the current
                 serial -> content-based cycle match, immune to clock skew)
      - backup : "Connected to server: callhome-<fqdn>:443" (timestamp-only)
    Plus the negative signal: "Failed to read callhome config ... no such file"
    in the current cycle means it's still blocked (callhomeservice not created).
    """
    serial = ctx.get("serial") or ""
    # Strong: serial-bearing line, current cycle, current serial.
    if serial:
        st_lines = current_cycle_lines(
            _lg(d, "diagnostic_agent", "serial_tenant"), ctx, require_serial=serial
        )
        if st_lines:
            return TICK, "diagnostic-agent (current cycle, serial %s): %s" % (
                serial,
                _last(st_lines)[:110],
            )
    # Backup: timestamp-only "Connected to server" line in the current cycle.
    conn = current_cycle_lines(_lg(d, "diagnostic_agent", "connected_to_server"), ctx)
    if conn:
        note = (
            ""
            if serial
            else " (could not content-verify serial — no current serial yet)"
        )
        return TICK, "diagnostic-agent (current cycle): %s%s" % (
            _last(conn)[:110],
            note,
        )
    # Negative: still failing to read callhome config in the current cycle.
    fails = current_cycle_lines(_lg(d, "diagnostic_agent", "failed_read_callhome"), ctx)
    if fails:
        return (
            CROSS,
            'diagnostic-agent still (current cycle): "Failed to read callhome config ... no such file" (%d lines)'
            % len(fails),
        )
    return (
        CROSS,
        'diagnostic-agent: no CURRENT-CYCLE "Connected to server: callhome-...:443" (only stale/prior-cycle, or none)',
    )


def chk_3d_callhome_reachable(d, ctx):
    ts = (d.get("status") or {}).get("tethering_status") or {}
    reach = (d.get("status") or {}).get("reachability_status") or {}
    ok = bool(ts.get("callhome_reachable")) and bool(reach.get("callhome"))
    return (
        TICK if ok else CROSS
    ), "tethering_status.callhome_reachable=%s; reachability.callhome=%s" % (
        ts.get("callhome_reachable"),
        reach.get("callhome"),
    )


# ---- STAGE 4: tethering status all-true ----
def chk_4_all_true(d, ctx):
    ts = (d.get("status") or {}).get("tethering_status") or {}
    bools = [
        "cfg_serial_file_synced",
        "cloud_serial_file_synced",
        "serial_files_match",
        "cfgagent_connected",
        "callhome_reachable",
        "required_pods_running",
    ]
    bad = [k for k in bools if not ts.get(k)]
    pop = ["serial", "tenant_url", "rest_token"]
    empt = [k for k in pop if not ts.get(k)]
    if not bad and not empt:
        return (
            TICK,
            "all 6 tethering booleans true; serial/tenant_url/rest_token populated",
        )
    return CROSS, "false/empty: %s %s" % (
        ",".join(bad) or "(booleans ok)",
        ",".join(empt) or "(fields ok)",
    )


# ---- STAGE 5: operational (current cycle) ----
def chk_5a_metrics(d, ctx):
    """Stage 5a: statsite is emitting metrics with the CURRENT serial in-cycle.

    Positive-signal based: a current-cycle "Sent metrics" line carrying the
    current serial means metrics ARE flowing now. Early-cycle "Serial not found"
    lines (before stage 2 assigned the serial) are expected and do NOT fail this
    check; we only cross if no in-cycle Sent-metrics-with-serial exists.
    """
    serial = ctx.get("serial") or ""
    if not serial:
        return (
            CROSS,
            "statsite: no current serial to verify metrics against (stage 2 not complete)",
        )
    sent = current_cycle_lines(
        _lg(d, "statsite", "sent_metrics"), ctx, require_serial=serial
    )
    if sent:
        return (
            TICK,
            'statsite (current cycle): "Sent metrics" with serial=%s (%d lines)'
            % (serial, len(sent)),
        )
    notfound = current_cycle_lines(_lg(d, "statsite", "serial_not_found"), ctx)
    if notfound:
        return (
            CROSS,
            'statsite (current cycle): no "Sent metrics" with serial yet; still seeing "Serial not found in cloud config" (%d lines)'
            % len(notfound),
        )
    return (
        CROSS,
        'statsite: no CURRENT-CYCLE "Sent metrics" with serial=%s (only stale/prior-cycle, or none)'
        % serial,
    )


def chk_5b_heartbeat_sync(d, ctx):
    """Stage 5b: heartbeat_sync is delivering heartbeats to callhome-agent.

    Positive-signal based: current-cycle callhome-nginx heartbeat 200 responses
    mean heartbeats are landing. We do NOT fail just because early-cycle
    "Registration token file not found" lines exist (those precede token
    application and are filtered by the cycle anchor anyway).
    """
    ts = (d.get("status") or {}).get("tethering_status") or {}
    rt = ts.get("rest_token") or ""
    beats = current_cycle_lines(_pg(d, "nginx_heartbeat_200"), ctx)
    if beats and rt:
        return (
            TICK,
            "heartbeat_sync healthy: %d current-cycle heartbeat 200s; rest_token set"
            % len(beats),
        )
    notfound = current_cycle_lines(_lg(d, "heartbeat_sync", "token_not_found"), ctx)
    if notfound:
        return (
            CROSS,
            'heartbeat_sync: %d current-cycle "Registration token file not found" lines; rest_token=%r'
            % (len(notfound), rt[:12]),
        )
    return CROSS, "heartbeat_sync: no current-cycle heartbeat 200s; rest_token=%r" % (
        rt[:12] or "unset"
    )


# ---- Always-ignorable checks ----
def chk_ign_ais_cert(d, ctx):
    cfg = d.get("cfg") or {}
    if cfg.get("ais_cert"):
        return TICK, "ais_cert.json present (AIS mode enrolled)"
    return (
        WARN,
        "ais_cert.json MISSING — ignorable: normal at basic tether; created when AIS mode is enabled",
    )


def chk_ign_obs_pods(d, ctx):
    pods = (d.get("pods") or {}).get("pods_all") or ""
    init_obs = bool(re.search(r"obs-metrics-agent.*Init:0/1", pods)) or bool(
        re.search(r"obs-metrics-release.*Init:0/1", pods)
    )
    if init_obs:
        return (
            WARN,
            "obs-metrics/telegraf pods in Init:0/1 — ignorable: pre-tenant; become Ready after tether (seen on fresh boxes)",
        )
    return TICK, "obs-metrics/telegraf pods not stuck in Init"


# ===========================================================================
# Registry of checks (chronological order)
# ===========================================================================
# Each entry: (stage_label, id, display_label, fn, kind)
#   kind = "critical" (stages 0-5 + preflight)  -> tick/cross for tethered targets,
#                                                    warn for S1/S3 (untethered-by-design)
#          "ignorable"                          -> always warn (or tick if present)
CRITICAL_CHECKS = [
    ("PRE", "pre_build", "Build identity & required pods", chk_pre_build),
    ("PRE", "pre_pods", "Required pods Running", chk_pre_pods),
    ("0", "0a", "Registration key written", chk_0a_token),
    ("0", "0b", "system.json licensekey set", chk_0b_system),
    ("0", "0c", "cfgagent.conf license_key real", chk_0c_cfgagent_conf),
    ("0", "0d", "config.json registrationkey JWT", chk_0d_config_jwt),
    ("0", "0e", "Certificate enrollment sequence", chk_0e_enroll_seq),
    ("0", "0f", "client.pem valid (CN=vpe:<did>, C=US)", chk_0f_cert),
    ("1", "1a", "configdist endpoint real", chk_1a_endpoint),
    ("1", "1b", "cfgagent WebSocket connected", chk_1b_ws_connected),
    ("1", "1c", "cfgagent_connected = true", chk_1c_cfgagent_connected),
    ("2", "2a", "callhomeservice endpoint file created", chk_2a_callhomeservice),
    ("2", "2b", "serial assigned + synced", chk_2b_serial),
    ("2", "2c", "cloud config populated", chk_2c_cloudconfig),
    ("2", "2d", "sfconfig + rest_api_token populated", chk_2d_sfconfig_resttoken),
    ("2", "2e", "tenant config populated", chk_2e_tenant),
    ("2", "2f", "cfgagent watchdog ECHO + cfgwatcher", chk_2f_watchdog_cfgwatcher),
    (
        "2",
        "2g",
        "remote/* endpoints match current tenant",
        chk_2g_remote_endpoints_match,
    ),
    ("2", "2h", "tenant dir = only current tenant", chk_2h_tenant_dir_current),
    ("3", "3a", "callhome-watcher endpoint found", chk_3a_watcher),
    ("3", "3b", "callhome-nginx serving heartbeats", chk_3b_nginx_heartbeat),
    ("3", "3c", "diagnostic-agent connected", chk_3c_diag),
    ("3", "3d", "callhome_reachable = true", chk_3d_callhome_reachable),
    ("4", "4", "Tethering status all-true", chk_4_all_true),
    ("5", "5a", "metrics flowing", chk_5a_metrics),
    ("5", "5b", "heartbeat sync healthy", chk_5b_heartbeat_sync),
]

IGNORABLE_CHECKS = [
    ("0", "0g", "ais_cert.json", chk_ign_ais_cert),
    ("PRE", "pre2", "obs-metrics/telegraf Init pods", chk_ign_obs_pods),
    ("2", "2i", "appliance content refreshed (cross-stack)", chk_2i_content_refreshed),
]


# ===========================================================================
# Scenario-confirmation checks (validate the detected scenario's markers)
# ===========================================================================
def confirm_s1(d, ctx):
    cfg = d.get("cfg") or {}
    remote = d.get("remote") or {}
    reach = (d.get("status") or {}).get("reachability_status") or {}
    rows = []
    rows.append(
        (
            "cfgagent.conf license_key = ABCD (placeholder)",
            "ABCD" in (cfg.get("cfgagent_conf") or ""),
        )
    )
    rows.append(
        (
            "configdist-core-pub = CFG_SERVER_ENDPOINT (placeholder)",
            "CFG_SERVER_ENDPOINT" in (remote.get("configdist_core_pub") or ""),
        )
    )
    rows.append(("registration_token.json MISSING", not cfg.get("registration_token")))
    rows.append(("client.pem MISSING", not cfg.get("client_pem_exists")))
    rows.append(("reachability.configservice = false", not reach.get("configservice")))
    rows.append(
        (
            "cloudserial.json empty (0 bytes)",
            not ((cfg.get("cloudserial") or {}).get("serial")),
        )
    )
    return rows


def confirm_s3(d, ctx):
    cfg = d.get("cfg") or {}
    remote = d.get("remote") or {}
    recon = cfg.get("recon_status") or {}
    dirs = d.get("dirs") or {}
    logs = d.get("logs") or {}
    rows = []
    rows.append(
        ("registration_token.json stale-present", bool(cfg.get("registration_token")))
    )
    rows.append(("client.pem stale-present", bool(cfg.get("client_pem_exists"))))
    rows.append(
        (
            "configdist-core-pub real fqdn (not placeholder)",
            "CFG_SERVER_ENDPOINT" not in (remote.get("configdist_core_pub") or "")
            and bool(remote.get("configdist_core_pub", "").strip()),
        )
    )
    rows.append(
        ("remote/callhomeservice ABSENT", not remote.get("callhomeservice_exists"))
    )
    rows.append(
        ("recon_status.reset_requested = true", bool(recon.get("reset_requested")))
    )
    rows.append(
        (
            "reset log sequence present",
            bool(logs.get("nsclib", {}).get("reset_retain"))
            or bool(logs.get("heartbeat_sync", {}).get("reset_completed")),
        )
    )
    rows.append(
        (
            "fastscan/aisecurity content persisted",
            dirs.get("fastscan_count", 0) > 0 or dirs.get("aisecurity_count", 0) > 0,
        )
    )
    rows.append(
        (
            "reset markers (kmip dir / stray /opt/ns/2)",
            bool(dirs.get("kmip_exists")) or bool(dirs.get("stray_2")),
        )
    )
    return rows


def confirm_s4(d, ctx):
    cfg = d.get("cfg") or {}
    dirs = d.get("dirs") or {}
    logs = d.get("logs") or {}
    recon = cfg.get("recon_status") or {}
    rows = []
    rows.append(
        (
            "recon_status.reset_requested = true (lingering)",
            bool(recon.get("reset_requested")),
        )
    )
    rows.append(
        (
            "reset markers (kmip dir / stray /opt/ns/2)",
            bool(dirs.get("kmip_exists")) or bool(dirs.get("stray_2")),
        )
    )
    rows.append(
        (
            "reset log sequence before re-enrollment",
            bool(logs.get("nsclib", {}).get("reset_retain"))
            or bool(logs.get("heartbeat_sync", {}).get("reset_completed")),
        )
    )
    rows.append(
        (
            "re-enrollment ran (nsclib enroll_success)",
            bool(logs.get("nsclib", {}).get("enroll_success")),
        )
    )
    return rows


# ===========================================================================
# Rendering
# ===========================================================================
class Color:
    GREEN = "\033[32m"
    RED = "\033[31m"
    YELLOW = "\033[33m"
    BOLD = "\033[1m"
    DIM = "\033[2m"
    RESET = "\033[0m"


_COLOR_ENABLED = None  # set in main(); None => auto (isatty)


def use_color():
    if _COLOR_ENABLED is not None:
        return _COLOR_ENABLED
    return sys.stdout.isatty()


def C(text, code):
    if not use_color():
        return text
    return code + text + Color.RESET


def mark_symbol(mark):
    if mark == TICK:
        return C("✓", Color.GREEN)
    if mark == CROSS:
        return C("✗", Color.RED)
    if mark == WARN:
        return C("⚠", Color.YELLOW)
    return "·"


def render_header(d, ctx, ip):
    ts = (d.get("status") or {}).get("tethering_status") or {}
    now = _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC")
    age = ctx.get("age_min")
    age_str = (
        ("enrolled %d min ago" % age) if age is not None else "enrollment age: unknown"
    )
    print(C("=" * 78, Color.DIM))
    print(
        C(
            "VPE Tethering Diagnostics — %s (%s, build %s)"
            % (ip, ctx.get("hostname"), ctx.get("build") or "?"),
            Color.BOLD,
        )
    )
    print(C("Captured: %s  |  %s" % (now, age_str), Color.DIM))
    print(C("=" * 78, Color.DIM))
    print()


def render_classification(scen, reason, confidence):
    print(C("=== SCENARIO CLASSIFICATION ===", Color.BOLD))
    print(
        "Detected: %s   [%s confidence]"
        % (C(SCENARIO_NAMES.get(scen, scen), Color.BOLD), confidence)
    )
    print("Reason: %s" % reason)
    print()


def render_check_line(stage, cid, label, mark, reason, first_fail=False, blocked=False):
    sym = mark_symbol(mark)
    # align label to ~40 chars
    lbl = ("%s %s" % (stage, cid)).strip()
    dot = "." * max(2, 44 - len(label) - len(lbl))
    extra = ""
    if first_fail:
        extra = "  " + C(
            "<- FIRST FAILURE (root cause); downstream blocked by this",
            Color.RED + Color.BOLD,
        )
    elif blocked:
        extra = "  " + C("(blocked by upstream failure)", Color.DIM)
    print("[%s] %-5s %-34s %s %s%s" % (sym, lbl, label, dot, reason, extra))


def evaluate_checks(d, ctx, scen):
    """Evaluate all critical + ignorable checks WITHOUT rendering.

    Returns (results, ignorable_results, first_cross_stage) where each result
    tuple is (stage, cid, label, mark, reason). For untethered-by-design
    scenarios (S1/S3), tethering stages 0-5 are downgraded to WARN (the state
    is not expected to be achieved there, but the reason still documents what
    is on disk). S2/S4/FAILING/IN-PROGRESS/UNKNOWN keep their real mark.
    """
    untethered_by_design = scen in ("S1", "S3")
    first_cross_cid = None
    first_cross_stage = None
    results = []
    for stage, cid, label, fn in CRITICAL_CHECKS:
        mark, reason = fn(d, ctx)
        if untethered_by_design and stage != "PRE":
            mark = WARN
            label = {"S1": "fresh, never tethered", "S3": "deprovisioned"}.get(
                scen, scen
            )
            reason = "not expected for this scenario (%s) — %s" % (label, reason)
        results.append((stage, cid, label, mark, reason))
        if mark == CROSS and first_cross_cid is None:
            first_cross_cid = cid
            first_cross_stage = stage
    ignorable_results = []
    for stage, cid, label, fn in IGNORABLE_CHECKS:
        mark, reason = fn(d, ctx)
        ignorable_results.append((stage, cid, label, mark, reason))
    return results, ignorable_results, first_cross_stage


def render_phase_block(d, ctx, scen):
    print(C("=== TETHERING PHASE VALIDATION (chronological) ===", Color.BOLD))
    results, ignorable_results, first_cross_stage = evaluate_checks(d, ctx, scen)
    first_cross_cid = None
    for _st, cid, _lbl, mark, _rs in results:
        if mark == CROSS:
            first_cross_cid = cid
            break
    for stage, cid, label, mark, reason in results:
        ff = mark == CROSS and cid == first_cross_cid
        bd = mark == CROSS and cid != first_cross_cid
        render_check_line(stage, cid, label, mark, reason, first_fail=ff, blocked=bd)
    # Ignorable checks (always shown)
    print()
    for stage, cid, label, mark, reason in ignorable_results:
        render_check_line(stage, cid, label, mark, reason)
    print()
    return results, ignorable_results, first_cross_stage


def render_scenario_confirmation(d, ctx, scen):
    if scen == "S1":
        title = "S1 (FRESH) confirmation markers"
        rows = confirm_s1(d, ctx)
    elif scen == "S3":
        title = "S3 (DEPROVISIONED) stale-marker confirmation"
        rows = confirm_s3(d, ctx)
    elif scen == "S4":
        title = "S4 (RE-TETHERED) history-marker confirmation"
        rows = confirm_s4(d, ctx)
    else:
        return
    print(C("=== %s ===" % title, Color.BOLD))
    for label, ok in rows:
        sym = mark_symbol(TICK if ok else CROSS)
        print("[%s] %s" % (sym, label))
    print()


def render_summary(d, ctx, scen, results, ignorable_results, first_cross_stage):
    ts = (d.get("status") or {}).get("tethering_status") or {}
    reach = (d.get("status") or {}).get("reachability_status") or {}
    all_results = results + ignorable_results
    ticks = sum(1 for _, _, _, m, _ in all_results if m == TICK)
    crosses = sum(1 for _, _, _, m, _ in all_results if m == CROSS)
    warns = sum(1 for _, _, _, m, _ in all_results if m == WARN)
    print(C("=== SUMMARY ===", Color.BOLD))
    if scen in ("S2", "S4"):
        tag = "SUCCESS" if scen == "S2" else "SUCCESS (re-tether)"
        age = ctx.get("age_min")
        age_s = (" (enrolled %d min ago)" % age) if age is not None else ""
        # Distinguish a true all-green success from "tethering complete but
        # operational (stage 5) not yet flowing" (common in the first few minutes
        # after tether — metrics/heartbeat take a bit longer to start).
        tether_phase_crosses = [
            r for r in results if r[3] == CROSS and r[0] in ("0", "1", "2", "3", "4")
        ]
        op_crosses = [r for r in results if r[3] == CROSS and r[0] == "5"]
        if not tether_phase_crosses and not op_crosses:
            print(
                C(
                    "TETHERING: %s — %s, all phases complete%s"
                    % (tag, "first tether" if scen == "S2" else "re-tethered", age_s),
                    Color.GREEN + Color.BOLD,
                )
            )
        elif not tether_phase_crosses and op_crosses:
            op_labels = ", ".join(r[2] for r in op_crosses)
            print(
                C(
                    "TETHERING COMPLETE (stages 0-4)%s — stage 5 operational not yet flowing (%s); usually transient, re-run in a few minutes."
                    % (age_s, op_labels),
                    Color.YELLOW + Color.BOLD,
                )
            )
        else:
            print(
                C(
                    "TETHERING: %s BUT %d tethering-phase failure(s) above — review the [✗] lines."
                    % (tag, len(tether_phase_crosses)),
                    Color.RED + Color.BOLD,
                )
            )
    elif scen == "S1":
        print(
            C(
                "FRESH — never tethered. No enrollment attempt detected. Apply a registration key to begin.",
                Color.YELLOW + Color.BOLD,
            )
        )
    elif scen == "S3":
        print(
            C(
                "DEPROVISIONED — previously tethered (reset retain-network completed), currently untethered. Stale artifacts present. Re-apply a registration key to re-tether.",
                Color.YELLOW + Color.BOLD,
            )
        )
    elif scen in ("FAILING", "IN-PROGRESS"):
        stage_label = first_cross_stage or "?"
        # find the first failing check's reason
        first_reason = ""
        for stage, cid, label, m, r in results:
            if m == CROSS:
                first_reason = r
                break
        age = ctx.get("age_min")
        if scen == "IN-PROGRESS":
            print(
                C(
                    "TETHERING IN PROGRESS — first incomplete stage: %s. Age %d min (<%d-min grace); re-run in a few minutes."
                    % (stage_label, age if age is not None else -1, TETHER_GRACE_MIN),
                    Color.YELLOW + Color.BOLD,
                )
            )
        else:
            print(
                C(
                    "TETHERING FAILING — stalled at STAGE %s. Age %s min (>= %d-min expectation)."
                    % (stage_label, age if age is not None else "?", TETHER_GRACE_MIN),
                    Color.RED + Color.BOLD,
                )
            )
        print(C("First unmet state: %s" % first_reason, Color.RED))
        hint = likely_cause_hint(first_cross_stage, d, ctx)
        if hint:
            print(C("Likely cause: " + hint, Color.YELLOW))
    else:
        print(C("UNKNOWN state — manual review needed.", Color.YELLOW + Color.BOLD))
    # identity line
    if ts.get("serial") or ctx.get("serial"):
        print(
            "Serial %s | Tenant %s (%s) | rest_token %s | identifier %s"
            % (
                ts.get("serial") or ctx.get("serial"),
                ts.get("tenant_url") or ctx.get("fqdn") or "-",
                ctx.get("tid") or "-",
                (ts.get("rest_token") or "")[:12] + "...",
                ctx.get("identifier") or "-",
            )
        )
    print(
        "%d expected states: %d %s, %d %s, %d %s."
        % (
            ticks + crosses + warns,
            ticks,
            C("✓ achieved", Color.GREEN),
            crosses,
            C("✗ failed", Color.RED),
            warns,
            C("⚠ ignorable", Color.YELLOW),
        )
    )
    sf = ctx.get("stale_filtered", 0)
    if sf:
        print(
            C(
                "(cycle-aware: ignored %d stale log line(s) from prior tether cycles; anchored to current enrollment %s)"
                % (
                    sf,
                    (
                        ctx["cycle_start_dt"].strftime("%Y-%m-%d %H:%M UTC")
                        if ctx.get("cycle_start_dt")
                        else "?"
                    ),
                ),
                Color.DIM,
            )
        )
    print()


def likely_cause_hint(stage, d, ctx):
    """One-line, non-prescriptive hint at the probable cause for a stalled stage."""
    if stage == "0":
        n = (d.get("logs") or {}).get("nsclib", {})
        fails = n.get("enroll_failed") or []
        last_fail = _last(fails) if fails else ""
        # CSR-subject-specific (the ENG-1007978 Country/State/Locality defect) —
        # typically surfaces as HTTP 500 "failed to sign CSR"/"Subject missing Country".
        if (
            "Registration endpoint not found" in last_fail
            or "token may be for a different environment" in last_fail
        ):
            return (
                "certificate enrollment failed — 'Registration endpoint not found': the registration "
                "token's environment/endpoint doesn't match this appliance's configured registration "
                "endpoint. Known cause: the token was generated for a DIFFERENT environment (e.g. a "
                "prod token applied to an NPE box, or vice-versa), or the appliance's registration "
                "endpoint config is wrong. Generate a FRESH registration token from the correct "
                "environment/tenant and re-apply it (`set system registrationkey <jwt>` then `save`)."
            )
        if "failed to sign CSR" in last_fail or "Subject missing Country" in last_fail:
            return (
                "certificate enrollment failed at TCS (failed to sign CSR / Subject missing Country). "
                "Known cause: TCS rejects a CSR missing Country/State/Locality (ENG-1007978) — "
                "either the appliance build predates the CSR-subject fix, or TCS/vpe-manager on the "
                "POP cluster is rejecting this CSR. Check vpe-manager logs on the POP cluster for "
                "'CSR signing failed'/'Subject missing Country'; ensure the appliance is on a build "
                "with the ENG-1007978 fix, then generate a FRESH registration token before re-saving."
            )
        # Generic HTTP status classification (4xx vs 5xx) via regex.
        m4xx = re.search(r"HTTP 4\d\d", last_fail)
        m5xx = re.search(r"HTTP 5\d\d", last_fail)
        if m4xx:
            code = m4xx.group(0)
            if "409" in code:
                return (
                    "enrollment failed with %s — the registration token has already been used (409). "
                    "Generate a FRESH registration token and re-apply it." % code
                )
            return (
                "enrollment failed with %s (client-side error at TCS/vpe-manager). Check the token is "
                "valid and not expired, the JWT is well-formed, and the tenant/fqdn match. See the full "
                "error above." % code
            )
        if m5xx:
            code = m5xx.group(0)
            return (
                "enrollment failed with %s (server-side error at TCS/vpe-manager on the POP cluster). "
                "Check vpe-manager logs on the POP cluster for 'CSR signing failed'/'Subject missing "
                "Country'/upstream errors; retry, and if it persists confirm TCS/vpe-manager health."
                % code
            )
        if _has(fails):
            return "certificate enrollment failed — see the failure message above (HTTP/connection error to callhome/TCS). Check nsclib.log + vpe-manager on the POP cluster."
        return "registration key / enrollment step did not complete — check nsclib.log around 'Starting VPE certificate enrollment' and confirm the JWT is valid and not expired."
    if stage == "1":
        if _pg(d, "cfgagent_gaierror"):
            return "cfgagent cannot resolve the configdist host — DNS or the configdist-core-pub endpoint file is wrong."
        return "cfgagent WebSocket to configdist not establishing — check cfgagent pod logs + configdist reachability from the box."
    if stage == "2":
        return (
            "cfgagent is connected but configdist is not pushing config/serial (callhomeservice file not created). "
            "Known pattern: the appliance identifier does not match the node record configdist expects "
            "(see memory: vpe-tethering-registration-flow-diagnostics, 1.1.36 did/identifier defect). "
            "Verify server-side the node is Registered->Authenticated for this license/serial."
        )
    if stage == "3":
        if _pg(d, "watcher_file_not_ready"):
            return "callhome-watcher still waiting on remote/callhomeservice — stage 2 (configdist push) hasn't completed for callhome."
        return "callhomeservice endpoint exists but callhome reachability failing — check callhome pod logs + mTLS (client.pem) + DNS to callhome-<fqdn>."
    if stage == "4":
        return "tethering_status not all-true — see which boolean is false above and trace back to its stage."
    if stage == "5":
        return "tethering reported complete but metrics/heartbeat not flowing — check statsite.log and heartbeat_sync.log; usually transient right after tether."
    return ""


# ===========================================================================
# JSON report builder (--json mode)
# ===========================================================================
# Per-check metadata: what it validates, the on-box sources it reads, and the
# representative command(s) the remote collector runs to gather the data shown
# in the DETAILS column. The collector runs as `sudo -n python3 -` over SSH,
# so file reads are equivalent to a root `cat`, and shell runs (kubectl /
# openssl / nslookup) are shown verbatim. Keyed by check id (cid).
CHECK_META = {
    "pre_build": {
        "what": "Pre-flight — 'is the box even up and sane?' Confirms SSH worked and the build version (nscli_config.version, e.g. 1.0.540) and hostname are readable. If this crosses, the whole collection is suspect (SSH partial failure / wrong box). It's the 'did we actually talk to a VPE' gate.",
        "sources": [
            "/opt/ns/cfg/nscli_config.version",
            "/opt/ns/cfg/platform_code",
            "/etc/hostname",
        ],
        "commands": ["cat /opt/ns/cfg/nscli_config.version", "cat /etc/hostname"],
    },
    "pre_pods": {
        "what": "Pre-flight — required pods Running. Checks status.tethering_status.required_pods_running=true AND that vpe-platform-cfgagent + callhome-agent are actually in `kubectl get pods` Running state (vault injector presence is a bonus). This catches the fresh-box trap: required_pods_running can be true even when pods are internally failing — so this check cross-validates the boolean against the real pod list. A cross means the k8s layer itself is broken (CrashLoopBackOff / not scheduled), which is upstream of any tethering logic.",
        "sources": [
            "/opt/ns/appliance/status.json (.tethering_status.required_pods_running)",
            "kubectl get pods -n default -o wide",
        ],
        "commands": [
            "sudo -n kubectl --kubeconfig /etc/rancher/k3s/k3s.yaml get pods -n default -o wide",
            "jq '.tethering_status.required_pods_running' /opt/ns/appliance/status.json",
        ],
    },
    "0a": {
        "what": "Stage 0 — registration key written. registration_token.json exists and has tenant_id, device_id, fqdn, license_key, and expired_at not in the past. Catches: no key applied (fresh), a malformed/partial token, or an expired token (the JWT exp has passed — a common real-world cause of 'save behaves weirdly'). This file is root-only, which is why the collector runs as `sudo -n python3 -`.",
        "sources": ["/opt/ns/cfg/registration_token.json (+ mtime)"],
        "commands": ["cat /opt/ns/cfg/registration_token.json  # read as root"],
    },
    "0b": {
        "what": "Stage 0 — system.json licensekey set. system.json has a real licensekey (not absent, not the ABCD placeholder) and was written this cycle (mtime >= anchor). Catches: key not persisted to system config, OR a stale system.json from a prior tether (deprovision empties it to {}, so a non-empty stale one is a red flag).",
        "sources": ["/opt/ns/cfg/system.json (+ mtime)"],
        "commands": ["cat /opt/ns/cfg/system.json"],
    },
    "0c": {
        "what": "Stage 0 — cfgagent.conf license_key real. The license_key: line in cfgagent.conf is a real hash, not the sample ABCD, and written this cycle. On a fresh box this file is literally the sample template (license_key: ABCD); deprovision reverts it to ABCD. So this is a clean 'has a real key been burned in' signal, and the mtime check ensures it's from this cycle.",
        "sources": ["/opt/ns/cfg/cfgagent.conf (+ mtime)"],
        "commands": [
            "cat /opt/ns/cfg/cfgagent.conf",
            "grep license_key /opt/ns/cfg/cfgagent.conf",
        ],
    },
    "0d": {
        "what": "Stage 0 — config.json registrationkey JWT. Decodes the JWT stored in config.json.system.registrationkey, checks exp not passed, and — critically — cross-checks the JWT did equals registration_token.json.device_id. If they differ, config.json still holds a prior cycle's JWT (stale), a real bug condition. This did-match is a content-based cycle guard that doesn't depend on timestamps.",
        "sources": ["/opt/ns/cfg/config.json", "/opt/ns/cfg/registration_token.json"],
        "commands": [
            "cat /opt/ns/cfg/config.json",
            'python3 -c \'import base64,json; print(json.loads(base64.urlsafe_b64decode(<jwt>.split(".")[1]+"="*(-len(<jwt>.split(".")[1])%4))))\'',
        ],
    },
    "0e": {
        "what": "Stage 0 — certificate enrollment sequence (the core 'save succeeded' check). Requires, in the current cycle: nsclib 'Starting VPE certificate enrollment for device vpe:<current did>' (timestamped, current did) and 'Received client certificate from management plane' (timestamped). The 'Certificate enrollment completed successfully' display line is corroborating (no own timestamp, so it can't be the primary signal). On a failure line (Certificate enrollment failed / Certificate request failed (HTTP 4xx|5xx) / failed to sign CSR / Subject missing Country) it surfaces the actual error and classifies it: 5xx -> server-side TCS/vpe-manager, 4xx -> client-side (409 = token already used -> fresh token), failed to sign CSR / Subject missing Country -> ENG-1007978 CSR/Country hint. This is the check that detects a failed save directly.",
        "sources": ["/opt/ns/log/nsclib.log"],
        "commands": [
            'grep -E "Starting VPE certificate enrollment|Received client certificate from management plane|Certificate enrollment completed successfully|Certificate enrollment failed|failed to sign CSR|Subject missing Country|HTTP [45][0-9][0-9]" /opt/ns/log/nsclib.log'
        ],
    },
    "0f": {
        "what": "Stage 0 — client.pem valid. The cert exists, openssl parse shows CN=vpe:<did> matching the current token, Country present (C=US) (absence -> the pre-ENG-1007978 CSR bug that TCS rejects), notAfter in the future, client.key + issuer_ca.pem present, and notBefore >= cycle_start (cert was re-issued this cycle, not a stale leftover from a prior tether that deprovision didn't remove). The notBefore-freshness check is what catches a box showing an old cert while claiming success.",
        "sources": [
            "/opt/ns/cfg/client.pem",
            "/opt/ns/cfg/client.key",
            "/opt/ns/cfg/issuer_ca.pem",
        ],
        "commands": [
            "openssl x509 -in /opt/ns/cfg/client.pem -noout -subject -issuer -dates"
        ],
    },
    "1a": {
        "what": "Stage 1 — configdist endpoint real. remote/configdist-core-pub host is not the literal placeholder 'CFG_SERVER_ENDPOINT' (the fresh-box default — cfgagent tries to DNS-resolve the word 'CFG_SERVER_ENDPOINT' and fails with gaierror). On a real tether it's rewritten to config-<tenant-fqdn>. This file persists across cycles (not re-touched on re-tether), so it's NOT mtime-gated — a real fqdn from a prior tether is still a usable endpoint for the current cycle. This check is 'is there a resolvable configdist endpoint at all.'",
        "sources": ["/opt/ns/appliance/common/remote/configdist-core-pub"],
        "commands": ["cat /opt/ns/appliance/common/remote/configdist-core-pub"],
    },
    "1b": {
        "what": "Stage 1 — cfgagent WebSocket connected. The cfgagent pod log has a current-cycle 'Successfully reconnected to wss://config-<fqdn>:443/configdist/sf?...&serial=<current serial>&identifier=...' line. Both guards matter: timestamp >= anchor (drops prior-cycle reconnects) and the URL carries the current serial (a reconnect with an old serial is from a prior tether). If cfgagent reconnects but serial= is empty, that's stage 1 succeeded, stage 2 not yet — so 1b still ticks and the missing serial is pinned at stage 2, not here (avoids mis-pinning the root cause). A gaierror -> DNS/endpoint problem.",
        "sources": ["pod log: vpe-platform-cfgagent (container cfgagent)"],
        "commands": [
            "sudo -n kubectl --kubeconfig /etc/rancher/k3s/k3s.yaml logs -n default <cfgagent-pod> -c cfgagent --tail=600  # then grep 'Successfully reconnected to wss://'"
        ],
    },
    "1c": {
        "what": "Stage 1 — cfgagent_connected = true. Cross-checks three live sources: status.json.tethering_status.cfgagent_connected, reachability_status.configservice, and the on-disk cfgagent_current_status file .status == 'connected'. Uses cfgagent_current_status, not the sibling cfgagent_connect_status, because the latter can hold a stale 'connected' written once at pod start and never updated (verified on a fresh box where they disagreed). These are live state, so no cycle filter needed.",
        "sources": [
            "/opt/ns/appliance/status.json",
            "/opt/ns/states/lccloudsync/cfgagent_current_status",
        ],
        "commands": [
            "jq '.tethering_status.cfgagent_connected, .reachability_status.configservice' /opt/ns/appliance/status.json",
            "cat /opt/ns/states/lccloudsync/cfgagent_current_status",
        ],
    },
    "2a": {
        "what": "Stage 2 — callhomeservice endpoint file created. remote/callhomeservice exists and was created this cycle (mtime >= anchor). This file is the single cleanest stage-2 tell — configdist pushes it. A stale one (mtime precedes anchor) is flagged STALE. Absent -> configdist hasn't pushed config -> stage 2 not reached.",
        "sources": ["/opt/ns/appliance/common/remote/callhomeservice (+ mtime)"],
        "commands": [
            "cat /opt/ns/appliance/common/remote/callhomeservice",
            "ls -l /opt/ns/appliance/common/remote/callhomeservice",
        ],
    },
    "2b": {
        "what": "Stage 2 — serial assigned + synced. serial non-empty, cloudserial.json.serial == /opt/ns/cloud/serial.serial, serial_files_match=true, and both files written this cycle. Catches: no serial assigned (the classic 'connected but never provisioned' stall), serial present but the two copies disagree (sync bug), or a stale serial from a prior tether (mtime check). Since each tether mints a new serial, a stale serial file is a real risk on multi-cycle boxes.",
        "sources": [
            "/opt/ns/appliance/status.json (.tethering_status.serial_files_match)",
            "/opt/ns/cfg/cloudserial.json",
            "/opt/ns/cloud/serial",
        ],
        "commands": [
            "cat /opt/ns/cfg/cloudserial.json",
            "cat /opt/ns/cloud/serial",
            "jq '.tethering_status.serial_files_match' /opt/ns/appliance/status.json",
        ],
    },
    "2c": {
        "what": "Stage 2 — cloud config populated. cloudconfig.json and pushed.json both > 1KB and written this cycle. pushed.json carries the tenant's DNS-intercepted-domains config; some tenants (e.g. prod without DNS interception) push little/no pushed.json, so an empty pushed.json is NOT by itself a failure — if the rest of stage 2 (sfconfig/serial/tenant) IS populated, this downgrades to WARN. Crosses only when the whole stage-2 push is missing (genuine configdist stall).",
        "sources": ["/opt/ns/cfg/cloudconfig.json", "/opt/ns/cloud/pushed.json"],
        "commands": ["ls -l /opt/ns/cfg/cloudconfig.json /opt/ns/cloud/pushed.json"],
    },
    "2d": {
        "what": "Stage 2 — sfconfig + rest_api_token populated. sfconfig.json > 1KB (it carries the tenant endpoints + SSH keys + tenant_id) and rest_api_token.json has a rest-token, both current-cycle. The rest_token is tenant-derived (same across appliances on a tenant), so its presence confirms the tenant config pushed.",
        "sources": ["/opt/ns/cloud/sfconfig.json", "/opt/ns/cloud/rest_api_token.json"],
        "commands": [
            "ls -l /opt/ns/cloud/sfconfig.json",
            "cat /opt/ns/cloud/rest_api_token.json",
        ],
    },
    "2e": {
        "what": "Stage 2 — tenant config populated. /opt/ns/tenant/<tenant_id>/ has > 5 files and the dir mtime is this cycle. Deprovision removes the tenant dir, so a stale-populated tenant dir (prior cycle) is caught by the mtime check.",
        "sources": ["/opt/ns/tenant/<tenant-id>/"],
        "commands": [
            "ls -l /opt/ns/tenant/<tenant-id>/  # directory listing + file count"
        ],
    },
    "2f": {
        "what": "Stage 2 corroboration — cfgagent watchdog ECHO + cfgwatcher. The cfgagent watchdog ECHO line carrying the current serial is the STRONG signal (cfgagent is connected and provisioned with a serial). The cfgwatcher 'Copying pushed.json -> cloudconfig.json' line only appears if configdist pushed a non-empty pushed.json, which some tenants (e.g. prod without DNS-intercepted config) don't. So: ECHO-with-serial present + copy missing -> WARN (not cross); ECHO-with-serial ALSO missing -> cross.",
        "sources": [
            "pod log: vpe-platform-cfgagent (container cfgagent)",
            "/opt/ns/log/cfgwatcher-cloudsync.log",
        ],
        "commands": [
            "sudo -n kubectl ... logs -n default <cfgagent-pod> -c cfgagent --tail=600  # grep activity_watchdog",
            'grep "Copying /opt/ns/cloud/pushed.json" /opt/ns/log/cfgwatcher-cloudsync.log',
        ],
    },
    "2g": {
        "what": "Stage 2 (cross-stack) — remote/* endpoints match current tenant. Every endpoint file under remote/ must have a host matching the CURRENT tenant fqdn. On a same-tenant re-tether all endpoints match (re-pushed or persist with the same fqdn). On a cross-tenant re-tether, any endpoint file NOT re-pushed by the new configdist keeps the OLD tenant's host -> mismatch -> the box would send logs / ssh-tunnel / UI to the wrong stack. This is the check that catches the 'configdist-core-pub not rewritten' / mixed-endpoints latent defects on a cross-stack move.",
        "sources": [
            "/opt/ns/appliance/common/remote/* (all endpoint files, e.g. configdist-core-pub, callhomeservice, messaging, ...)"
        ],
        "commands": [
            'for f in /opt/ns/appliance/common/remote/*; do echo "== $f =="; grep -o \'"host"[^,]*\' "$f"; done'
        ],
    },
    "2h": {
        "what": "Stage 2 (cross-stack) — tenant dir = only current tenant. /opt/ns/tenant/ must contain ONLY the current tenant's dir. A same-tenant re-tether has one dir (the current tenant, re-populated). A cross-tenant re-tether WITHOUT a prior deprovision may leave the OLD tenant's dir in place -> two tenant config dirs on disk -> mixed/wrong tenant config. Catches a re-tether to a different stack where deprovision wasn't run first.",
        "sources": ["/opt/ns/tenant/<tenant-id>/ (all tenant dirs)"],
        "commands": ["ls -l /opt/ns/tenant/"],
    },
    "2i": {
        "what": "Stage 2 (cross-stack, informational) — appliance content refreshed. fastscan_appliance / aisecurityservice_appliance content refresh status. On a same-tenant re-tether these content dirs are NOT re-pushed (idempotent) — fine. On a CROSS-tenant re-tether, stale content means the box runs the OLD tenant's AIS patterns / TSS prefilters. WARN (not cross) because same-tenant staleness is expected, and if AIS/TSS service templates aren't enabled on the tenant the content is inception default (pushed once at first tether, never refreshed) and staleness is normal — only a concern if AIS/TSS is enabled and content differs per tenant.",
        "sources": [
            "/opt/ns/cfg/fastscan_appliance (+ mtime)",
            "/opt/ns/cfg/aisecurityservice_appliance (+ mtime)",
        ],
        "commands": [
            "ls -l /opt/ns/cfg/fastscan_appliance /opt/ns/cfg/aisecurityservice_appliance"
        ],
    },
    "3a": {
        "what": "Stage 3 — callhome-watcher endpoint found. Current-cycle 'Endpoint configuration found' + 'Certificates found, proxying with mTLS' in the callhome-watcher pod log. Before the endpoint file appears, the watcher logs 'File not ready ... callhomeservice ... not found' every 5s — if that's still current, 3a crosses with that reason (pointing back to stage 2 not having created callhomeservice).",
        "sources": ["pod log: callhome-agent (container callhome-watcher)"],
        "commands": [
            "sudo -n kubectl ... logs -n default <callhome-pod> -c callhome-watcher --tail=200  # grep 'Endpoint configuration found|proxying with mTLS|File not ready'"
        ],
    },
    "3b": {
        "what": "Stage 3 — callhome-nginx serving heartbeats. Current-cycle 'POST /vpemanager/v1/heartbeat HTTP/1.1\" 200' lines in the callhome-nginx pod log. This is the heartbeat actually landing and returning 200 — the positive counterpart to the fresh-box 'Waiting for callhome configuration...' state.",
        "sources": ["pod log: callhome-agent (container callhome-nginx)"],
        "commands": [
            "sudo -n kubectl ... logs -n default <callhome-pod> -c callhome-nginx --tail=200  # grep 'POST /vpemanager/v1/heartbeat.*200'"
        ],
    },
    "3c": {
        "what": "Stage 3 — diagnostic-agent connected. Prefers the serial-bearing line 'Serial number: <current serial>, Tenant ID: <tid>, Server callhome-<fqdn>:443' (content-based cycle match — strongest), falling back to the timestamp-only 'Connected to server: callhome-<fqdn>:443'. If only 'Failed to read callhome config ... no such file' is current, 3c crosses (callhome still blocked).",
        "sources": ["/opt/ns/log/diagnostic-agent.log"],
        "commands": [
            'grep -E "Connected to server: callhome|Serial number:.*Tenant ID:|Failed to read callhome config" /opt/ns/log/diagnostic-agent.log'
        ],
    },
    "3d": {
        "what": "Stage 3 — callhome_reachable = true. Live-state cross-check: status.json.tethering_status.callhome_reachable AND reachability_status.callhome both true. No cycle filter (live state).",
        "sources": ["/opt/ns/appliance/status.json"],
        "commands": [
            "jq '.tethering_status.callhome_reachable, .reachability_status.callhome' /opt/ns/appliance/status.json"
        ],
    },
    "4": {
        "what": "Stage 4 — tethering status all-true. All six booleans true (cfg_serial_file_synced, cloud_serial_file_synced, serial_files_match, cfgagent_connected, callhome_reachable, required_pods_running) AND serial/tenant_url/rest_token populated. This is the status.json .tethering_status summary field — the same JSON `nsshell status tethering` returns. It's a rollup, so if it crosses the per-stage checks above tell you which boolean is false and why.",
        "sources": ["/opt/ns/appliance/status.json (.tethering_status)"],
        "commands": ["jq '.tethering_status' /opt/ns/appliance/status.json"],
    },
    "5a": {
        "what": "Stage 5 — metrics flowing. Current-cycle 'Sent metrics' line in statsite.log carrying the current serial. Positive-signal-based (not 'no Serial-not-found lines') — because early in the cycle, before the serial is assigned, statsite legitimately logs 'Serial not found in cloud config'. So we tick only when the success signal (Sent metrics + current serial) appears in-cycle; cross only if it never appears. A stale 'Sent metrics' with an old serial is filtered by the serial match.",
        "sources": ["/opt/ns/log/statsite.log"],
        "commands": [
            'grep -E "Sent metrics|Serial not found in cloud config" /opt/ns/log/statsite.log'
        ],
    },
    "5b": {
        "what": "Stage 5 — heartbeat sync healthy. Current-cycle callhome-nginx heartbeat 200s exist AND rest_token is set. Positive-signal — early-cycle 'Registration token file not found' lines (before the token was applied) are filtered by the cycle anchor, so they don't false-fail this.",
        "sources": [
            "pod log: callhome-agent (container callhome-nginx)",
            "/opt/ns/appliance/status.json (.tethering_status.rest_token)",
        ],
        "commands": [
            "sudo -n kubectl ... logs -n default <callhome-pod> -c callhome-nginx --tail=200  # grep heartbeat 200",
            "jq '.tethering_status.rest_token' /opt/ns/appliance/status.json",
        ],
    },
    "0g": {
        "what": "Ignorable — ais_cert.json. This is the AIS cert, not the mgmt/callhome cert. It's only enrolled when AIS mode is enabled, so it's missing on a normal basic tether — hence warn, not cross. 'ais_cert.json does not exist' is logged repeatedly on healthy tethered boxes.",
        "sources": ["/opt/ns/cfg/ais_cert.json"],
        "commands": ["cat /opt/ns/cfg/ais_cert.json"],
    },
    "pre2": {
        "what": "Ignorable — obs-metrics/telegraf Init pods. On a fresh box the obs/telegraf pods sit in Init:0/1 until tenant config unblocks them; they flip to 1/1 Running after tether. So Init:0/1 is warn not cross — it's a known pre-tenant state, not a tethering failure.",
        "sources": ["kubectl get pods -A"],
        "commands": [
            "sudo -n kubectl --kubeconfig /etc/rancher/k3s/k3s.yaml get pods -A"
        ],
    },
}


def build_json_report(
    d, ctx, scen, reason, confidence, ip, results, ignorable_results, first_cross_stage
):
    """Build a machine-readable dict mirroring the text report, for the UI to
    render as a structured checklist. Does not print."""

    def _scen_name(code):
        # Strip the leading internal "S# — " code so user-facing text never
        # shows the S1/S2/S3/S4 label.
        name = SCENARIO_NAMES.get(code, code)
        return re.sub(r"^S\d+\s*[—-]\s*", "", name)

    ts = (d.get("status") or {}).get("tethering_status") or {}
    all_results = results + ignorable_results
    ticks = sum(1 for r in all_results if r[3] == TICK)
    crosses = sum(1 for r in all_results if r[3] == CROSS)
    warns = sum(1 for r in all_results if r[3] == WARN)
    nas = sum(1 for r in all_results if r[3] == NA)

    first_cross_cid = None
    for _st, cid, _lbl, mark, _rs in results:
        if mark == CROSS:
            first_cross_cid = cid
            break

    age = ctx.get("age_min")
    status = "UNKNOWN"
    message = ""
    hint = ""
    if scen in ("S2", "S4"):
        tether_phase_crosses = [
            r for r in results if r[3] == CROSS and r[0] in ("0", "1", "2", "3", "4")
        ]
        op_crosses = [r for r in results if r[3] == CROSS and r[0] == "5"]
        if not tether_phase_crosses and not op_crosses:
            status = "SUCCESS" if scen == "S2" else "SUCCESS_RETHETHER"
            message = "TETHERING: %s — %s, all phases complete%s" % (
                "SUCCESS" if scen == "S2" else "SUCCESS (re-tether)",
                "first tether" if scen == "S2" else "re-tethered",
                (" (enrolled %d min ago)" % age) if age is not None else "",
            )
        elif not tether_phase_crosses and op_crosses:
            status = "COMPLETE_OP_PENDING"
            message = (
                "TETHERING COMPLETE (stages 0-4)%s — stage 5 operational not yet flowing (%s); usually transient, re-run in a few minutes."
                % (
                    (" (enrolled %d min ago)" % age) if age is not None else "",
                    ", ".join(r[2] for r in op_crosses),
                )
            )
        else:
            status = "SUCCESS_WITH_FAILURES"
            message = (
                "TETHERING: %s BUT %d tethering-phase failure(s) — review the failed rows."
                % (
                    "SUCCESS" if scen == "S2" else "SUCCESS (re-tether)",
                    len(tether_phase_crosses),
                )
            )
    elif scen == "S1":
        status = "FRESH"
        message = "FRESH — never tethered. No enrollment attempt detected. Apply a registration key to begin."
    elif scen == "S3":
        status = "DEPROVISIONED"
        message = "DEPROVISIONED — previously tethered (reset retain-network completed), currently untethered. Re-apply a registration key to re-tether."
    elif scen in ("FAILING", "IN-PROGRESS"):
        first_reason = ""
        for r in results:
            if r[3] == CROSS:
                first_reason = r[4]
                break
        if scen == "IN-PROGRESS":
            status = "IN_PROGRESS"
            message = (
                "TETHERING IN PROGRESS — first incomplete stage: %s. Age %d min (<%d-min grace); re-run in a few minutes."
                % (
                    first_cross_stage or "?",
                    age if age is not None else -1,
                    TETHER_GRACE_MIN,
                )
            )
        else:
            status = "FAILING"
            message = (
                "TETHERING FAILING — stalled at STAGE %s. Age %s min (>= %d-min expectation)."
                % (
                    first_cross_stage or "?",
                    age if age is not None else "?",
                    TETHER_GRACE_MIN,
                )
            )
        hint = likely_cause_hint(first_cross_stage, d, ctx)

    def row(r, ignorable=False):
        stage, cid, label, mark, freason = r
        meta = CHECK_META.get(cid, {})
        return {
            "stage": stage,
            "cid": cid,
            "label": label,
            "mark": mark,
            "reason": freason,
            "firstFail": mark == CROSS and cid == first_cross_cid,
            "blocked": mark == CROSS and cid != first_cross_cid,
            "ignorable": ignorable,
            "what": meta.get("what", ""),
            "sources": meta.get("sources", []),
            "commands": meta.get("commands", []),
        }

    # Confirmation markers (S1/S3/S4 scenario evidence) — list of {label, ok}.
    confirmation = []
    if scen == "S1":
        confirmation = [
            {"label": lbl, "ok": bool(ok)} for lbl, ok in confirm_s1(d, ctx)
        ]
    elif scen == "S3":
        confirmation = [
            {"label": lbl, "ok": bool(ok)} for lbl, ok in confirm_s3(d, ctx)
        ]
    elif scen == "S4":
        confirmation = [
            {"label": lbl, "ok": bool(ok)} for lbl, ok in confirm_s4(d, ctx)
        ]

    report = {
        "ip": ip,
        "hostname": ctx.get("hostname") or "ns-vpe",
        "build": ctx.get("build") or "",
        "captured": _dt.datetime.utcnow().strftime("%Y-%m-%d %H:%M UTC"),
        "ageMin": age,
        "scenario": scen,
        "scenarioName": _scen_name(scen),
        "confidence": confidence,
        "reason": reason,
        "status": status,
        "summaryMessage": message,
        "likelyCause": hint,
        "firstFailStage": first_cross_stage,
        "identity": {
            "serial": ts.get("serial") or ctx.get("serial") or "",
            "tenantUrl": ts.get("tenant_url") or ctx.get("fqdn") or "",
            "tenantId": ctx.get("tid") or "",
            "restToken": (ts.get("rest_token") or "")[:12]
            + ("..." if ts.get("rest_token") else ""),
            "identifier": ctx.get("identifier") or "",
        },
        "counts": {
            "total": ticks + crosses + warns + nas,
            "ticks": ticks,
            "crosses": crosses,
            "warns": warns,
            "na": nas,
        },
        "staleFiltered": ctx.get("stale_filtered", 0),
        "cycleAnchor": (
            ctx["cycle_start_dt"].strftime("%Y-%m-%d %H:%M UTC")
            if ctx.get("cycle_start_dt")
            else None
        ),
        "checks": [row(r) for r in results],
        "ignorableChecks": [row(r, ignorable=True) for r in ignorable_results],
        "confirmation": confirmation,
        # Raw on-box status.json slices, byte-identical to `nsshell status
        # tethering` (.tethering_status) and the reachability panel, for the UI's
        # raw-status view. Read-only; no transformation.
        "tetheringStatus": ts,
        "reachabilityStatus": (d.get("status") or {}).get("reachability_status") or {},
    }
    return report


# ===========================================================================
# Main
# ===========================================================================
def main():
    ap = argparse.ArgumentParser(
        description="Read-only VPE tethering diagnostic. Classifies the tethering scenario (S1/S2/S3/S4/failing) and lists each expected state with tick/cross/exclamation.",
        epilog="Reads /opt/ns/appliance/status.json + on-box files/logs over SSH. Performs NO mutating actions.",
    )
    ap.add_argument("ip", nargs="?", help="VPE IP address (omit if using --from-json)")
    ap.add_argument(
        "--user",
        default=DEFAULT_USER,
        help="SSH user (default nsadmin / $VPE_SSH_USER)",
    )
    ap.add_argument(
        "--pass",
        dest="password",
        default=DEFAULT_PASS,
        help="SSH password (default nsappliance / $VPE_SSH_PASS)",
    )
    ap.add_argument(
        "--sshpass",
        default=None,
        help="path to sshpass binary (auto-detected if omitted)",
    )
    ap.add_argument("--no-color", action="store_true", help="disable ANSI color")
    ap.add_argument(
        "--json",
        action="store_true",
        help="emit a machine-readable JSON report instead of the text report",
    )
    ap.add_argument(
        "--raw",
        action="store_true",
        help="dump the raw collected JSON and exit (debug)",
    )
    ap.add_argument(
        "--from-json",
        metavar="PATH",
        help="load a previously --raw-saved JSON snapshot instead of SSHing (offline re-evaluation / testing)",
    )
    args = ap.parse_args()

    if args.no_color:
        global _COLOR_ENABLED
        _COLOR_ENABLED = False

    if not args.ip and not args.from_json:
        die("provide a VPE IP, or use --from-json to evaluate a saved snapshot.")
    if args.ip and args.from_json:
        die("specify either an IP or --from-json, not both.")

    sshpass = find_sshpass(args.sshpass)
    if not sshpass and not args.from_json:
        die("sshpass binary not found. Install it or pass --sshpass /path/to/sshpass.")

    if args.from_json:
        try:
            with open(args.from_json) as f:
                d = json.load(f)
        except Exception as e:
            die("could not load --from-json snapshot %s: %s" % (args.from_json, e))
    else:
        d = collect_from_vpe(args.ip, args.user, args.password, sshpass)

    if args.raw:
        print(json.dumps(d, indent=2))
        return

    ctx = build_context(d)
    scen, reason, confidence = classify(d, ctx)

    if args.json:
        results, ign_results, first_cross = evaluate_checks(d, ctx, scen)
        report = build_json_report(
            d, ctx, scen, reason, confidence, args.ip, results, ign_results, first_cross
        )
        print(json.dumps(report, indent=2))
        return

    render_header(d, ctx, args.ip)
    render_classification(scen, reason, confidence)
    render_scenario_confirmation(d, ctx, scen)
    results, ign_results, first_cross = render_phase_block(d, ctx, scen)
    render_summary(d, ctx, scen, results, ign_results, first_cross)


if __name__ == "__main__":
    main()
