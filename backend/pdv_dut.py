"""Appliance PDV DUT Version utility backend (Flask Blueprint).

For each per-site Jenkins "appliance-pdv-<site>" job, reads the job's *default*
APPLIANCE_IP_ADDRESS parameter (the Configure-parameters value, not a build),
SSHes into that DUT appliance, and captures `show version-info`.

SSH uses the same password-only wrapper as vpe_tether_diag.py: sshpass with
nsadmin / nsappliance (env-overridable via PDV_SSH_USER / PDV_SSH_PASS) and
PreferredAuthentications=password + PubkeyAuthentication=no so a key-laden agent
doesn't trip "Too many authentication failures". nsshell needs a tty (it crashes
without one on `stty size`), so we force a pty with `ssh -tt` and drive the
session interactively: wait for the first prompt, send `show version-info`,
parse the version lines, send `exit`, and kill the process group as a safety
net. (Newer builds consume stdin during nsshell startup, so a command piped
before the prompt appears is silently dropped — hence the prompt wait.)

All 13 sites are refreshed in parallel (one thread per site) every 30 min by a
background scheduler; a manual refresh is available via POST /api/pdv-dut/refresh.
The last successful dataset is served while a refresh is running.

Jenkins Basic-auth is read from env JENKINS_AUTH ("user:token") or a git-ignored
file ~/.jenkins_appliance_token (mode 600) — never committed, never sent to the
UI. No credentials appear in the UI at all (the utility is fully backend-driven).

Registered by app.py via create_pdv_dut_bp().
"""

import base64
import json
import os
import re
import select
import signal
import subprocess
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

from flask import Blueprint, jsonify

# --- configuration (overridable via env) ---
HOME = os.path.expanduser("~")
JENKINS_URL = os.environ.get(
    "JENKINS_URL",
    "http://appliance-jenkins-int.appliance.nc1.iad0.nsscloud.net:8080",
).rstrip("/")
JENKINS_AUTH_FILE = os.environ.get(
    "JENKINS_AUTH_FILE", os.path.join(HOME, ".jenkins_appliance_token")
)
REFRESH_INTERVAL = int(os.environ.get("PDV_REFRESH_INTERVAL", "1800"))  # 30 min
SSH_USER = os.environ.get("PDV_SSH_USER", "nsadmin")
SSH_PASS = os.environ.get("PDV_SSH_PASS", "nsappliance")
SSH_TIMEOUT = int(os.environ.get("PDV_SSH_TIMEOUT", "30"))
JENKINS_TIMEOUT = int(os.environ.get("PDV_JENKINS_TIMEOUT", "15"))

# (job-suffix, display name) — order here is the table order.
PDV_SITES = [
    ("am2", "AM2"),
    ("fr4", "FR4"),
    ("sv5", "SV5"),
    ("bom3", "BOM3"),
    ("dfw3", "DFW3"),
    ("fra2", "FRA2"),
    ("lon3", "LON3"),
    ("mel2", "MEL2"),
    ("ruh1", "RUH1"),
    ("sin2", "SIN2"),
    ("sjc1", "SJC1"),
    ("sjc2", "SJC2"),
    ("zur2", "ZUR2"),
]

# Fields emitted by `show version-info`, in display order. `threat-feed` keeps
# its hyphenated key (valid JSON key); the frontend labels it "Threat-feed".
FIELDS = ["software", "content", "dpop", "oplp", "rollback", "threat-feed", "urldb"]
_FIELD_SET = set(FIELDS)

# defense-in-depth: the IP comes from Jenkins (trusted) and is passed as an ssh
# argv element (never through a shell), so this just keeps bogus input out.
_IP_RE = re.compile(r"^(?:(?:\d{1,3}\.){3}\d{1,3}|[A-Za-z0-9.-]+)$")
# `field           : value` lines from show version-info.
_VER_RE = re.compile(r"^\s*([a-z][a-z-]*)\s*:\s*(\S+)\s*$")
# nsshell prompt -> hostname, e.g. "SIN2-vv180> ".
_HOST_RE = re.compile(r"^([A-Za-z0-9][A-Za-z0-9-]*)>\s")
# a line ending in the nsshell prompt "> " (no trailing newline) — we wait for
# this before sending `show version-info`, because newer builds consume stdin
# during startup and would drop a command sent before the prompt appears.
_PROMPT_TAIL_RE = re.compile(r">\s*$")

# --- shared state ---
_lock = threading.Lock()
_state = {
    "rows": [],
    "lastRefresh": None,  # ISO string, UTC
    "refreshing": False,
    "lastError": None,
}
_started = False


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _jenkins_auth():
    """Basic-auth 'user:token' from env JENKINS_AUTH or ~/.jenkins_appliance_token."""
    a = os.environ.get("JENKINS_AUTH")
    if a:
        return a.strip()
    try:
        with open(JENKINS_AUTH_FILE) as f:
            return f.read().strip()
    except OSError:
        return None


def _fetch_ip(site):
    """Return (ip, None) or (None, error) from the job's default parameters."""
    auth = _jenkins_auth()
    if not auth:
        return None, "Jenkins auth not configured (set JENKINS_AUTH or %s)" % JENKINS_AUTH_FILE
    url = (
        "%s/job/appliance-pdv-%s/api/json?tree="
        "property[parameterDefinitions[name,defaultParameterValue[value]]]"
    ) % (JENKINS_URL, site)
    req = urllib.request.Request(url)
    req.add_header("Authorization", "Basic " + base64.b64encode(auth.encode()).decode())
    try:
        with urllib.request.urlopen(req, timeout=JENKINS_TIMEOUT) as r:
            data = json.loads(r.read().decode("utf-8", "replace"))
    except urllib.error.HTTPError as exc:
        return None, "Jenkins HTTP %s for %s" % (exc.code, site)
    except Exception as exc:  # noqa: BLE001
        return None, "Jenkins fetch failed for %s: %s" % (site, exc)

    for prop in data.get("property", []) or []:
        for p in prop.get("parameterDefinitions", []) or []:
            if p.get("name") == "APPLIANCE_IP_ADDRESS":
                dp = p.get("defaultParameterValue") or {}
                v = (dp.get("value") or "").strip()
                if v:
                    return v, None
                return None, "APPLIANCE_IP_ADDRESS default is empty for %s" % site
    return None, "APPLIANCE_IP_ADDRESS parameter not found for %s" % site


def _fetch_version(ip):
    """SSH to the DUT, drive `show version-info` through nsshell, parse it.

    nsshell needs a tty (it crashes on `stty size` without one), so we force a
    pty with `ssh -tt`. It has no EOF/quit exit, but `exit` leaves the shell
    cleanly. Crucially, newer appliance builds consume stdin during nsshell
    startup, so a command piped in before the prompt appears is silently
    dropped — therefore we drive the session interactively: read output until
    the first prompt is shown, *then* send `show version-info`, parse the
    version lines as they arrive, send `exit`, and terminate the process group
    as a safety net (nsshell can still hang on some builds).

    Returns ({"versions": {...}, "hostname": str}, None) or (None, error).
    """
    if not _IP_RE.match(ip):
        return None, "invalid appliance IP: %s" % ip
    cmd = [
        "sshpass", "-p", SSH_PASS, "ssh", "-tt",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "PreferredAuthentications=password",
        "-o", "PubkeyAuthentication=no",
        "-o", "IdentitiesOnly=yes",
        "-o", "ConnectTimeout=15",
        "%s@%s" % (SSH_USER, ip),
        "hostname; PAGER=cat nsshell",
    ]
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True,  # own process group -> can killpg ssh+nsshell
        )
    except FileNotFoundError:
        return None, "sshpass not found on server"
    except Exception as exc:  # noqa: BLE001
        return None, "SSH failed: %s" % exc

    fd = proc.stdout.fileno()
    chunks = []
    versions = {}
    hostname = ""
    sent_cmd = False
    sent_exit = False
    deadline = time.time() + SSH_TIMEOUT

    def _send(data):
        try:
            proc.stdin.write(data)
            proc.stdin.flush()
        except Exception:  # noqa: BLE001 - process may have exited
            pass

    try:
        while time.time() < deadline:
            r, _, _ = select.select([fd], [], [], 1.0)
            if not r:
                continue
            try:
                chunk = os.read(fd, 4096)
            except OSError:
                chunk = b""
            if not chunk:
                break  # EOF — ssh/nsshell exited
            chunks.append(chunk.decode("utf-8", "replace"))
            joined = "".join(chunks)
            for ln in joined.splitlines():
                m = _VER_RE.match(ln)
                if m and m.group(1) in _FIELD_SET:
                    versions[m.group(1)] = m.group(2)
                else:
                    hm = _HOST_RE.match(ln)
                    if hm:
                        hostname = hm.group(1)
            # Wait for the first prompt (banner fully printed) before sending
            # the command — avoids the newer-build startup-stdin-drop.
            if (
                not sent_cmd
                and "Netskope Appliance" in joined
                and _PROMPT_TAIL_RE.search(joined)
            ):
                sent_cmd = True
                _send(b"show version-info\n")
            # Once we have every field, send `exit` and stop reading.
            if sent_cmd and len(versions) >= len(FIELDS) and not sent_exit:
                sent_exit = True
                _send(b"exit\n")
                time.sleep(0.4)
                break
        if not sent_exit:
            _send(b"exit\n")
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except Exception:  # noqa: BLE001
            try:
                proc.terminate()
            except Exception:  # noqa: BLE001
                pass
        try:
            proc.wait(timeout=5)
        except Exception:  # noqa: BLE001
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except Exception:  # noqa: BLE001
                try:
                    proc.kill()
                except Exception:  # noqa: BLE001
                    pass

    joined = "".join(chunks)
    if not versions:  # final pass over the whole capture
        for ln in joined.splitlines():
            m = _VER_RE.match(ln)
            if m and m.group(1) in _FIELD_SET:
                versions[m.group(1)] = m.group(2)
    if not versions:
        err = ""
        try:
            raw = proc.stderr.read() if proc.stderr else b""
            err = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        except Exception:  # noqa: BLE001
            pass
        hint = ""
        for src in (joined, err):
            for needle in (
                "Permission denied",
                "Connection refused",
                "Could not resolve",
                "No route to host",
                "Connection timed out",
            ):
                if needle in src:
                    hint = needle
                    break
            if hint:
                break
        if hint:
            return None, "%s (%s)" % (hint, ip)
        return None, "no version-info from %s%s" % (
            ip,
            (": " + err.strip()[:160]) if err.strip() else "",
        )
    ordered = {f: versions[f] for f in FIELDS if f in versions}
    # The remote command prints `hostname` first (before the nsshell banner), so
    # the first stdout line is the real host name — authoritative over the
    # nsshell prompt, which on some boxes is the literal default "nsappliance".
    first_line = ""
    for ln in joined.splitlines():
        s = ln.strip()
        if s and not s.startswith("=") and "Netskope" not in s and "Permanently added" not in s:
            first_line = s
            break
    if first_line and re.match(r"^[A-Za-z0-9][A-Za-z0-9._-]*$", first_line):
        hostname = first_line
    elif not hostname:
        hm = re.search(r"^([A-Za-z0-9][A-Za-z0-9-]*)>\s", joined, re.M)
        if hm:
            hostname = hm.group(1)
    return {"versions": ordered, "hostname": hostname}, None


def _do_one(site, display):
    ip, err = _fetch_ip(site)
    if err:
        return {
            "site": site,
            "displayName": display,
            "ip": None,
            "reachable": False,
            "versions": None,
            "hostname": "",
            "error": err,
        }
    ver, verr = _fetch_version(ip)
    return {
        "site": site,
        "displayName": display,
        "ip": ip,
        "reachable": not verr,
        "versions": ver["versions"] if ver else None,
        "hostname": (ver or {}).get("hostname", ""),
        "error": verr,
    }


def _do_refresh():
    """Run one refresh cycle across all sites in parallel. No-ops if already running."""
    with _lock:
        if _state["refreshing"]:
            return
        _state["refreshing"] = True
        _state["lastError"] = None

    try:
        with ThreadPoolExecutor(max_workers=len(PDV_SITES)) as ex:
            rows = list(ex.map(lambda sd: _do_one(*sd), PDV_SITES))
        # restore defined table order (ex.map preserves input order, but be safe)
        order = {s: i for i, (s, _) in enumerate(PDV_SITES)}
        rows.sort(key=lambda r: order.get(r["site"], 999))
        with _lock:
            _state["rows"] = rows
            _state["lastRefresh"] = _iso(time.time())
        ok = sum(1 for r in rows if r["reachable"])
        print(
            "[pdv-dut] refresh complete: %d/%d sites reached" % (ok, len(rows)),
            flush=True,
        )
    except Exception as exc:  # noqa: BLE001 - keep previous data, surface error
        with _lock:
            _state["lastError"] = str(exc)
        print("[pdv-dut] refresh failed: %s" % exc, flush=True)
    finally:
        with _lock:
            _state["refreshing"] = False


def _refresh_async():
    threading.Thread(target=_do_refresh, daemon=True).start()


def _scheduler():
    """Background loop: refresh every PDV_REFRESH_INTERVAL seconds."""
    while True:
        time.sleep(REFRESH_INTERVAL)
        _do_refresh()


def create_pdv_dut_bp(cfg=None):  # noqa: ARG001 - cfg unused (self-contained)
    """Build the blueprint and start the background scheduler once."""
    global _started
    bp = Blueprint("pdv_dut", __name__)

    if not _started:
        _started = True
        _refresh_async()
        threading.Thread(target=_scheduler, daemon=True).start()
        print(
            "[pdv-dut] scheduler started: %d sites, every %ds"
            % (len(PDV_SITES), REFRESH_INTERVAL),
            flush=True,
        )

    @bp.route("/api/pdv-dut/data")
    def data():
        with _lock:
            snapshot = {
                "rows": _state["rows"],
                "lastRefresh": _state["lastRefresh"],
                "refreshing": _state["refreshing"],
                "lastError": _state["lastError"],
            }
        return jsonify(snapshot)

    @bp.route("/api/pdv-dut/refresh", methods=["POST"])
    def refresh():
        _refresh_async()
        return jsonify({"ok": True, "refreshing": True})

    return bp
