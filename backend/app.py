#!/usr/bin/env python3
"""RI Stacks Version Dashboard - Flask backend.

Serves the built React app (../dist) and a small JSON API backed by
stacks_build_version.sh --json, which gathers per-cluster pod images and
internal packages via kubectl against the kubeconfigs in ~/rancher.

Stale-while-refreshing: the last successful dataset is served while a refresh
is running. A background scheduler triggers a refresh every 5 minutes; a
manual refresh is available via POST /api/refresh.
"""
import glob
import json
import os
import re
import subprocess
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from flask import Flask, jsonify, request, send_from_directory

import types

from provisioner import create_provisioner_bp
from tenant_finder import create_tenant_finder_bp
from vpe_diag import create_vpe_diag_bp

# --- configuration (overridable via env) ---
HOME = os.path.expanduser("~")
RANCHER_DIR = os.environ.get("RANCHER_DIR", os.path.join(HOME, "rancher"))
SCRIPT_PATH = os.environ.get(
    "STACKS_SCRIPT", os.path.join(HOME, "rca-dashboard", "stacks_build_version.sh")
)
DIST_DIR = os.environ.get(
    "DIST_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist")
)
PORT = int(os.environ.get("PORT", "5001"))
REFRESH_INTERVAL = int(os.environ.get("REFRESH_INTERVAL", "300"))  # 5 min
KUBECTL_DIR = os.path.join(HOME, ".local", "bin")
RUN_TIMEOUT = int(os.environ.get("RUN_TIMEOUT", "180"))

# PATH so the script can find kubectl (~/.local/bin) and jq (/usr/bin)
RUN_ENV = dict(os.environ)
RUN_ENV["PATH"] = "{}:/usr/local/bin:/usr/bin:/bin".format(KUBECTL_DIR)

NPE_RE = re.compile(r"qa01|stg01|devint|npe02|fed1mp|perf01")

# yaml filename -> short, uppercase display name shown in the table
DISPLAY_NAMES = {
    "c1-sv5.yaml": "SV5",
    "c4-am2.yaml": "AM2",
    "c4-fr4.yaml": "FR4",
    "c4-sjc1.yaml": "SJC1",
    "stork-bom3-mp-prod-bom3-nc1.yaml": "BOM3",
    "stork-dfw3-mp-prod-dfw3-nc1.yaml": "DFW3",
    "stork-fra2-mp-prod-fra2-nc1.yaml": "FRA2",
    "stork-lon3-mp-prod-lon3-nc1.yaml": "LON3",
    "stork-mel2-mp-mel2-nc1.yaml": "MEL2",
    "stork-ruh1-mp-prod-ruh1-nc1.yaml": "RUH1",
    "stork-sin2-mp-prod-sin2-nc1.yaml": "SIN2",
    "stork-sjc2-mp-prod-sjc2-nc1.yaml": "SJC2",
    "stork-zur2-mp-prod-zur2-nc1.yaml": "ZUR2",
    "stork-stg01-mp-iad0-nc4.yaml": "STG01",
    "stork-qa01-mp-npe-iad0-nc1.yaml": "QA01",
    "stork-devint-automation-iad0-nc1.yaml": "DEVINT",
    "stork-npe02-mp-iad0-nc4.yaml": "NPE02",
    "stork-fed1mp-iad0-nc1.yaml": "FED1MP",
    "stork-perf01-mp-iad0-nc6.yaml": "PERF01",
}

app = Flask(
    __name__, static_folder=os.path.join(DIST_DIR, "assets"), static_url_path="/assets"
)

# Provisioning utility (VPE UI flag) — reuses the constants above
_cfg = types.SimpleNamespace(
    RANCHER_DIR=RANCHER_DIR, RUN_ENV=RUN_ENV, DISPLAY_NAMES=DISPLAY_NAMES, NPE_RE=NPE_RE
)
app.register_blueprint(create_provisioner_bp(_cfg))
app.register_blueprint(create_tenant_finder_bp(_cfg))
app.register_blueprint(create_vpe_diag_bp(_cfg))


def _list_stacks():
    out = []
    for path in sorted(glob.glob(os.path.join(RANCHER_DIR, "*.yaml"))):
        fname = os.path.basename(path)
        out.append(
            {
                "name": fname,
                "displayName": DISPLAY_NAMES.get(
                    fname, os.path.basename(fname).rsplit(".", 1)[0].upper()
                ),
                "env": _classify_env(fname),
            }
        )
    return out


# --- shared state ---
_lock = threading.Lock()
_state: Dict[str, Any] = {
    "stacks": [],
    "last_refresh": None,  # ISO string, UTC
    "refreshing": False,
    "last_error": None,
}


def _iso(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _classify_env(name: str) -> str:
    return "npe" if NPE_RE.search(name) else "prod"


def _rancher_last_refresh() -> Optional[str]:
    """Newest mtime among the rancher kubeconfig files -> ISO UTC, or None."""
    try:
        mtimes = [
            os.path.getmtime(p) for p in glob.glob(os.path.join(RANCHER_DIR, "*.yaml"))
        ]
    except OSError:
        return None
    if not mtimes:
        return None
    return _iso(max(mtimes))


def _run_script() -> List[Dict[str, Any]]:
    """Run stacks_build_version.sh --json and return the parsed stacks list."""
    proc = subprocess.run(
        ["bash", SCRIPT_PATH, "--json"],
        cwd=RANCHER_DIR,
        env=RUN_ENV,
        capture_output=True,
        text=True,
        timeout=RUN_TIMEOUT,
    )
    if proc.returncode != 0:
        raise RuntimeError(
            "script exited {}: stderr={}".format(
                proc.returncode, proc.stderr.strip()[:500]
            )
        )
    payload = json.loads(proc.stdout)
    stacks = payload.get("stacks", [])
    for s in stacks:
        s["env"] = _classify_env(s.get("name", ""))
        s["displayName"] = DISPLAY_NAMES.get(
            s.get("name", ""),
            os.path.basename(s.get("name", "")).rsplit(".", 1)[0].upper(),
        )
        s.setdefault("images", [])
        s.setdefault("packages", {})
    return stacks


def _do_refresh() -> None:
    """Run one refresh cycle. No-ops if one is already running."""
    with _lock:
        if _state["refreshing"]:
            return
        _state["refreshing"] = True
        _state["last_error"] = None

    try:
        stacks = _run_script()
        with _lock:
            _state["stacks"] = stacks
            _state["last_refresh"] = _iso(time.time())
            _state["last_error"] = None
        print(
            "[rca-dashboard] refresh complete: {} stacks".format(len(stacks)),
            flush=True,
        )
    except Exception as exc:  # noqa: BLE001 - keep previous data, surface error
        with _lock:
            _state["last_error"] = str(exc)
        print("[rca-dashboard] refresh failed: {}".format(exc), flush=True)
    finally:
        with _lock:
            _state["refreshing"] = False


def _refresh_async() -> None:
    threading.Thread(target=_do_refresh, daemon=True).start()


def _scheduler() -> None:
    """Background loop: refresh every REFRESH_INTERVAL seconds."""
    while True:
        time.sleep(REFRESH_INTERVAL)
        _do_refresh()


# --- routes ---
@app.route("/")
def index():
    return send_from_directory(DIST_DIR, "index.html")


@app.route("/favicon.svg")
def favicon_svg():
    return send_from_directory(DIST_DIR, "favicon.svg", mimetype="image/svg+xml")


@app.route("/favicon.ico")
def favicon_ico():
    # no binary ico shipped; serve the svg so legacy requests still get an icon
    svg = os.path.join(DIST_DIR, "favicon.svg")
    if os.path.isfile(svg):
        return send_from_directory(DIST_DIR, "favicon.svg", mimetype="image/svg+xml")
    return ("", 204)


@app.route("/api/data")
def api_data():
    with _lock:
        snapshot = {
            "stacks": _state["stacks"],
            "last_refresh": _state["last_refresh"],
            "refreshing": _state["refreshing"],
        }
    resp = {
        "refreshing": snapshot["refreshing"],
        "lastRefresh": snapshot["last_refresh"],
        "rancherLastRefresh": _rancher_last_refresh(),
        "stacks": snapshot["stacks"],
    }
    if _state.get("last_error") and not snapshot["stacks"]:
        resp["error"] = _state["last_error"]
    return jsonify(resp)


@app.route("/api/refresh", methods=["POST"])
def api_refresh():
    _refresh_async()
    return jsonify({"ok": True, "refreshing": True})


@app.route("/api/stacks", methods=["GET"])
def api_stacks():
    return jsonify({"stacks": _list_stacks()})


_POD_RE = re.compile(r"^[A-Za-z0-9._-]+$")


def _clean_kubectl_err(s):
    """Drop kubectl API-discovery warning lines (memcache.go / 'couldn't get
    resource list') so the surfaced error is the real one (e.g. RBAC 403)."""
    return "\n".join(
        ln
        for ln in (s or "").splitlines()
        if ln
        and "memcache.go" not in ln
        and "couldn't get resource list" not in ln
        and "Unhandled Error" not in ln
    ).strip()


@app.route("/api/restart-deployment", methods=["POST"])
def api_restart_deployment():
    """Rolling-restart the Deployment that owns the given pod.

    Derives the Deployment via ownerReferences: pod -> ReplicaSet -> Deployment,
    then `kubectl rollout restart deployment/<dep>`.
    """
    body = request.get_json(silent=True) or {}
    stack = body.get("stack", "")
    pod = (body.get("pod", "") or "").strip()
    name = os.path.basename(stack)
    kc = os.path.join(RANCHER_DIR, name)
    print("[restart-deployment] stack={} pod={}".format(stack, pod), flush=True)
    if not name.endswith(".yaml") or not os.path.isfile(kc):
        print("[restart-deployment] unknown stack -> 400", flush=True)
        return jsonify({"ok": False, "error": "unknown stack: " + stack}), 400
    if not pod or not _POD_RE.match(pod):
        print("[restart-deployment] invalid pod name -> 400", flush=True)
        return jsonify({"ok": False, "error": "invalid pod name"}), 400

    def run_kubectl(args, timeout=40):
        return subprocess.run(
            ["kubectl", "--kubeconfig", kc, "--request-timeout=20"] + args,
            env=RUN_ENV,
            capture_output=True,
            text=True,
            timeout=timeout,
        )

    try:
        # pod -> ReplicaSet
        proc = run_kubectl(
            [
                "get",
                "pod",
                pod,
                "-n",
                "risk-insights",
                "-o",
                'jsonpath={.metadata.ownerReferences[?(@.kind=="ReplicaSet")].name}',
            ]
        )
        rs = (proc.stdout or "").strip()
        print(
            "[restart-deployment] pod->RS rc={} rs={!r} err={!r}".format(
                proc.returncode, rs, (proc.stderr or "")[:200]
            ),
            flush=True,
        )
        if proc.returncode != 0 or not rs:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": _clean_kubectl_err(proc.stderr)
                        or "could not find a ReplicaSet owner for pod " + pod,
                        "output": (proc.stdout or "") + (proc.stderr or ""),
                    }
                ),
                200,
            )
        # ReplicaSet -> Deployment
        proc = run_kubectl(
            [
                "get",
                "rs",
                rs,
                "-n",
                "risk-insights",
                "-o",
                'jsonpath={.metadata.ownerReferences[?(@.kind=="Deployment")].name}',
            ]
        )
        dep = (proc.stdout or "").strip()
        print(
            "[restart-deployment] RS->Dep rc={} dep={!r} err={!r}".format(
                proc.returncode, dep, (proc.stderr or "")[:200]
            ),
            flush=True,
        )
        if proc.returncode != 0 or not dep:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": _clean_kubectl_err(proc.stderr)
                        or "could not find a Deployment owner for ReplicaSet " + rs,
                        "output": (proc.stdout or "") + (proc.stderr or ""),
                    }
                ),
                200,
            )
        # rollout restart the deployment (all replicas)
        proc = run_kubectl(
            ["rollout", "restart", "deployment/" + dep, "-n", "risk-insights"],
            timeout=60,
        )
        print(
            "[restart-deployment] rollout restart deployment/{} rc={} out={!r} err={!r}".format(
                dep,
                proc.returncode,
                (proc.stdout or "")[:200],
                (proc.stderr or "")[:200],
            ),
            flush=True,
        )
        if proc.returncode != 0:
            return (
                jsonify(
                    {
                        "ok": False,
                        "error": (
                            _clean_kubectl_err(proc.stderr)
                            or _clean_kubectl_err(proc.stdout)
                            or "rollout restart failed"
                        )[:500],
                        "deployment": dep,
                        "output": (proc.stdout or "") + (proc.stderr or ""),
                    }
                ),
                200,
            )
        return jsonify(
            {
                "ok": True,
                "deployment": dep,
                "message": "deployment/{} restarted (rolling out)".format(dep),
            }
        )
    except subprocess.TimeoutExpired:
        print("[restart-deployment] kubectl timed out", flush=True)
        return jsonify({"ok": False, "error": "kubectl timed out"}), 200


@app.errorhandler(404)
def spa_fallback(err):  # noqa: ARG001
    # let client-side routing / unknown paths fall back to the app shell
    return send_from_directory(DIST_DIR, "index.html")


def main():
    if not os.path.isdir(DIST_DIR):
        print(
            "[rca-dashboard] WARNING: dist/ not found at {}".format(DIST_DIR),
            flush=True,
        )
    if not os.path.isfile(SCRIPT_PATH):
        print(
            "[rca-dashboard] WARNING: script not found at {}".format(SCRIPT_PATH),
            flush=True,
        )

    # initial refresh + scheduler
    _refresh_async()
    threading.Thread(target=_scheduler, daemon=True).start()

    print(
        "[rca-dashboard] serving on 0.0.0.0:{} (dist={})".format(PORT, DIST_DIR),
        flush=True,
    )
    app.run(host="0.0.0.0", port=PORT, threaded=True)


if __name__ == "__main__":
    main()
