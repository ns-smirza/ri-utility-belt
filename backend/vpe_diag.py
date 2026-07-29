"""VPE Tethering Diagnosis utility backend (Flask Blueprint).

Wraps the read-only `vpe_tether_diag.py` script. The script SSHes into a VPE
(nsadmin / nsappliance via sshpass, matching the manual workflow), runs a
read-only collector on the box, classifies the tethering scenario (S1/S2/S3/S4
/ failing / unknown), and emits a structured JSON report (per-stage
tick/cross/warn rows + summary + identity) via its --json mode.

This blueprint exposes one endpoint that runs the script against a caller-
supplied VPE IP and returns the parsed structured report for the UI to render
as a checklist table. No credentials flow through the UI — the script's
built-in sshpass defaults (nsadmin / nsappliance) are used, overridable only
via server-side env vars (VPE_SSH_USER / VPE_SSH_PASS).

Registered by app.py via create_vpe_diag_bp(cfg); cfg provides RUN_ENV.
"""

import json
import os
import re
import subprocess
import time

from flask import Blueprint, jsonify, request

# The script lives next to this module in the deployed server dir.
SCRIPT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "vpe_tether_diag.py"
)
# The script's own SSH collection timeout is 90s; give the subprocess a little
# headroom for process startup + local rendering.
RUN_TIMEOUT = 120

# Accept an IPv4 address or a DNS hostname (label[.label]+). This is a
# defense-in-depth input check — the IP is passed as a subprocess argv element
# (never through a shell), so it cannot inject commands; this just keeps bogus
# input from reaching the script.
_IP_RE = re.compile(
    r"^(?:(?:\d{1,3}\.){3}\d{1,3}|[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?)*)$"
)
# SSH user must be a sane POSIX username (passed as a subprocess argv element,
# never through a shell, so this is defense-in-depth only).
_USER_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")


def create_vpe_diag_bp(cfg):
    bp = Blueprint("vpe_diag", __name__)
    run_env = cfg.RUN_ENV

    @bp.route("/api/vpe-diag/run", methods=["POST"])
    def run():
        body = request.get_json(silent=True) or {}
        ip = (body.get("ip", "") or "").strip()
        user = (body.get("user", "") or "").strip()
        password = body.get("password", "")
        # password is free-form (may contain symbols); passed as an argv element
        # to the script -> sshpass, never through a shell, so no injection risk.
        if isinstance(password, str):
            password = password.strip()
        else:
            password = ""

        if not ip:
            return jsonify({"ok": False, "error": "VPE IP is required"}), 400
        if not _IP_RE.match(ip):
            return (
                jsonify({"ok": False, "error": "Invalid VPE IP or hostname: " + ip}),
                400,
            )
        if user and not _USER_RE.match(user):
            return jsonify({"ok": False, "error": "Invalid SSH username: " + user}), 400
        if not os.path.isfile(SCRIPT_PATH):
            return (
                jsonify(
                    {"ok": False, "error": "vpe_tether_diag.py not found on server"}
                ),
                500,
            )

        # --json => structured report (header + classification + per-stage
        # tick/cross/warn rows + summary + identity) for the UI table render.
        # Optional --user/--pass override the script's sshpass defaults
        # (nsadmin / nsappliance) only when provided.
        cmd = ["python3", SCRIPT_PATH, ip, "--json"]
        if user:
            cmd += ["--user", user]
        if password:
            cmd += ["--pass", password]
        start = time.time()
        try:
            proc = subprocess.run(
                cmd,
                env=run_env,
                capture_output=True,
                text=True,
                timeout=RUN_TIMEOUT,
            )
        except subprocess.TimeoutExpired:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "diagnostic timed out after {}s talking to {}".format(
                            RUN_TIMEOUT, ip
                        ),
                        "durationSec": RUN_TIMEOUT,
                    }
                ),
                200,
            )
        except Exception as exc:  # noqa: BLE001
            return (
                jsonify({"ok": False, "error": "failed to run script: " + str(exc)}),
                200,
            )

        duration = round(time.time() - start, 1)
        out = (proc.stdout or "").rstrip()
        err = (proc.stderr or "").strip()

        # The script exits 2 (die()) on SSH/parse failures, printing the reason
        # to stderr. Exit 0 with a JSON report on stdout == success.
        if proc.returncode != 0:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": err or "script exited {}".format(proc.returncode),
                        "output": out,
                        "returncode": proc.returncode,
                        "durationSec": duration,
                    }
                ),
                200,
            )

        try:
            report = json.loads(out)
        except ValueError as exc:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": "script returned non-JSON output: {}".format(exc),
                        "output": out[:2000],
                        "stderr": err,
                        "returncode": proc.returncode,
                        "durationSec": duration,
                    }
                ),
                200,
            )

        report["durationSec"] = duration
        return jsonify({"ok": True, "report": report, "stderr": err})

    return bp
