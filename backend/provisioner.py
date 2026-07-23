"""Provisioning utility backend (Flask Blueprint).

Manages feature-flag "features" for a tenant on a given stack by exec-ing
into the cluster's callhomeservice pod and curl-ing the in-cluster
provisioner-pycore service from inside the pod (mirroring the manual
workflow: read /opt/ns/common/remote/provisioner-pycore to discover the
host, then GET/POST /client/config?tenantid=...).

A "feature" is a named group of one or more flags. Currently:
  - vpe-ui        : nplan5663_vpe_setting_enabled
  - ai-guardrails : nplan5283_ai_security, nplan5663_vpe_setting_enabled,
                    nplan6445_aiguardrails_vpe

Registered by app.py via create_provisioner_bp(cfg); cfg provides
RANCHER_DIR, RUN_ENV, DISPLAY_NAMES, NPE_RE.
"""
import glob
import json
import os
import re
import subprocess

from flask import Blueprint, jsonify, request

ENDPOINT_FILE = "/opt/ns/common/remote/provisioner-pycore"
KUBECTL_TIMEOUT = 60
CURL_MAX_TIME = "20"
_FLAG_RE = re.compile(r"^[A-Za-z0-9_]+$")

# Feature registry — each feature is a named group of flags.
FEATURES = [
    {
        "key": "vpe-ui",
        "label": "VPE Beta",
        "description": "NEXT-GEN VPE Beta tab",
        "flags": ["nplan5663_vpe_setting_enabled"],
    },
    {
        "key": "ai-guardrails",
        "label": "AI Guardrails",
        "description": "AI Guardrails (3 flags)",
        "flags": [
            "nplan5283_ai_security",
            "nplan5663_vpe_setting_enabled",
            "nplan6445_aiguardrails_vpe",
        ],
    },
]


class ProvisionerError(Exception):
    """Carries a human message + the raw output to surface on failure."""

    def __init__(self, message, output=""):
        super().__init__(message)
        self.message = message
        self.output = output


def _feature(key):
    for f in FEATURES:
        if f["key"] == key:
            return f
    return None


def _state_of(value):
    if value == "1":
        return True, "enabled"
    if value == "0":
        return False, "disabled"
    return None, "not_found"


def _parse_flags(raw):
    """Accept a comma-separated string or a list; trim spaces, drop empties,
    dedupe preserving order, validate names. Returns a list."""
    if isinstance(raw, list):
        items = raw
    elif isinstance(raw, str):
        items = raw.split(",")
    else:
        return []
    seen = []
    for it in items:
        f = (it or "").strip()
        if not f:
            continue
        if not _FLAG_RE.match(f):
            raise ProvisionerError("Invalid flag name: " + f)
        if f not in seen:
            seen.append(f)
    return seen


def _resolve_flags(flags_raw, feature_key):
    """Prefer an explicit flags list (Custom); else resolve a built-in feature."""
    flags = _parse_flags(flags_raw) if flags_raw is not None and flags_raw != "" else []
    if flags:
        return flags
    if feature_key:
        feat = _feature(feature_key)
        if not feat:
            raise ProvisionerError("unknown feature: " + feature_key)
        return feat["flags"]
    raise ProvisionerError("no flags or feature specified")


def create_provisioner_bp(cfg):
    bp = Blueprint("provisioner", __name__)
    rancher_dir = cfg.RANCHER_DIR
    run_env = cfg.RUN_ENV
    display_names = cfg.DISPLAY_NAMES
    npe_re = cfg.NPE_RE

    def classify(name):
        return "npe" if npe_re.search(name) else "prod"

    def run(cmd):
        return subprocess.run(
            cmd, env=run_env, capture_output=True, text=True, timeout=KUBECTL_TIMEOUT
        )

    def _stack_path(stack):
        name = os.path.basename(stack)
        path = os.path.join(rancher_dir, name)
        if not name.endswith(".yaml") or not os.path.isfile(path):
            raise ProvisionerError("Unknown stack: " + stack)
        return name, path

    def _discover(stack):
        """Return (kubectl_exec_prefix, host, port, ns, pod, container)."""
        name, kc = _stack_path(stack)
        base = ["kubectl", "--kubeconfig", kc]

        p = run(base + ["get", "namespaces", "--no-headers"])
        if p.returncode != 0:
            raise ProvisionerError(
                "kubectl get namespaces failed for " + name, (p.stdout or "") + (p.stderr or "")
            )
        ns = None
        for line in p.stdout.splitlines():
            parts = line.split()
            if parts and "callhome" in parts[0].lower():
                ns = parts[0]
                break
        if not ns:
            raise ProvisionerError("No callhomeservice namespace found in " + name, p.stdout)

        p = run(base + ["-n", ns, "get", "pods", "--no-headers"])
        if p.returncode != 0:
            raise ProvisionerError(
                "kubectl get pods failed in " + ns, (p.stdout or "") + (p.stderr or "")
            )
        pod = None
        for line in p.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 3 and "callhomeservice-callhome" in parts[0] and parts[2] == "Running":
                pod = parts[0]
                break
        if not pod:
            raise ProvisionerError("No running callhomeservice-callhome pod in " + ns, p.stdout)

        p = run(base + ["-n", ns, "get", "pod", pod, "-o", "jsonpath={.spec.containers[0].name}"])
        container = p.stdout.strip() or None

        exec_prefix = base + ["-n", ns, "exec", pod]
        if container:
            exec_prefix += ["-c", container]

        p = run(exec_prefix + ["--", "cat", ENDPOINT_FILE])
        if p.returncode != 0:
            raise ProvisionerError(
                "Could not read " + ENDPOINT_FILE + " in pod " + pod,
                (p.stdout or "") + (p.stderr or ""),
            )
        try:
            ep = json.loads(p.stdout)
        except ValueError as exc:
            raise ProvisionerError("Invalid endpoint file JSON: " + str(exc), p.stdout)
        host = ep.get("host")
        port = ep.get("port", 80)
        if not host:
            raise ProvisionerError("Endpoint file has no host", p.stdout)

        return exec_prefix, host, port, ns, pod, container

    def _curl(exec_prefix, host, port, tenant, method, flag=None, value=None):
        url = "http://{}:{}/client/config?tenantid={}".format(host, port, tenant)
        cmd = exec_prefix + ["--", "curl", "-s", "--max-time", CURL_MAX_TIME]
        if method == "POST":
            body = json.dumps({flag: value})
            cmd += ["-X", "POST", "-H", "Content-Type: application/json", "-d", body]
        cmd += [url]

        p = run(cmd)
        if p.returncode != 0:
            raise ProvisionerError(
                "curl {} failed inside pod".format(method),
                (p.stdout or "") + (p.stderr or ""),
            )
        raw = p.stdout
        try:
            payload = json.loads(raw)
        except ValueError:
            raise ProvisionerError("Provisioner returned non-JSON response", raw)
        return payload, raw

    def _check(stack, tenant, flags):
        exec_prefix, host, port, _ns, _pod, _c = _discover(stack)
        payload, raw = _curl(exec_prefix, host, port, tenant, "GET")
        if payload.get("status") != "success":
            msg = payload.get("message") or payload.get("error") or "provisioner error"
            raise ProvisionerError(msg, raw)
        data = payload.get("data") or {}
        out = []
        all_enabled = True
        for f in flags:
            v = data.get(f)
            enabled, state = _state_of(v)
            if enabled is not True:
                all_enabled = False
            out.append({"flag": f, "value": v, "enabled": enabled, "state": state})
        return {"ok": True, "flags": out, "allEnabled": all_enabled}

    def _set(stack, tenant, value, flags):
        exec_prefix, host, port, _ns, _pod, _c = _discover(stack)
        results = []
        ok_count = 0
        for f in flags:
            try:
                payload, raw = _curl(exec_prefix, host, port, tenant, "POST", flag=f, value=value)
                if payload.get("status") == "success":
                    results.append({"flag": f, "ok": True, "output": raw})
                    ok_count += 1
                else:
                    msg = payload.get("message") or payload.get("error") or "provisioner error"
                    results.append({"flag": f, "ok": False, "error": msg, "output": raw})
            except ProvisionerError as exc:
                results.append({"flag": f, "ok": False, "error": exc.message, "output": exc.output})

        verified = None
        verify_error = None
        verify_output = None
        try:
            verified = _check(stack, tenant, flags)
        except ProvisionerError as exc:
            verify_error = exc.message
            verify_output = exc.output

        all_match = False
        if verified:
            want = value == "1"
            all_match = all(item.get("enabled") is want for item in verified["flags"])

        return {
            "ok": ok_count == len(flags),
            "value": value,
            "results": results,
            "summary": {"ok": ok_count, "fail": len(flags) - ok_count, "total": len(flags)},
            "verified": verified,
            "verifiedAllMatched": all_match,
            "verifyError": verify_error,
            "verifyOutput": verify_output,
        }

    # --- routes ---
    @bp.route("/api/provisioner/features", methods=["GET"])
    def features():
        return jsonify({"features": FEATURES})

    @bp.route("/api/provisioner/stacks", methods=["GET"])
    def stacks():
        out = []
        for path in sorted(glob.glob(os.path.join(rancher_dir, "*.yaml"))):
            fname = os.path.basename(path)
            out.append(
                {
                    "name": fname,
                    "displayName": display_names.get(
                        fname, os.path.basename(fname).rsplit(".", 1)[0].upper()
                    ),
                    "env": classify(fname),
                }
            )
        return jsonify({"stacks": out})

    @bp.route("/api/provisioner/check", methods=["GET"])
    def check():
        stack = request.args.get("stack", "")
        tenant = (request.args.get("tenant", "") or "").strip()
        feature_key = request.args.get("feature", "")
        flags_raw = request.args.get("flags", "")
        if not tenant:
            return jsonify({"ok": False, "error": "tenant is required"}), 400
        try:
            flags = _resolve_flags(flags_raw, feature_key)
            result = _check(stack, tenant, flags)
            result["feature"] = feature_key or "custom"
            return jsonify(result)
        except ProvisionerError as exc:
            return jsonify({"ok": False, "error": exc.message, "output": exc.output}), 200
        except subprocess.TimeoutExpired:
            return jsonify({"ok": False, "error": "operation timed out"}), 200
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 200

    @bp.route("/api/provisioner/set", methods=["POST"])
    def set_flag():
        body = request.get_json(silent=True) or {}
        stack = body.get("stack", "")
        tenant = (body.get("tenant", "") or "").strip()
        value = str(body.get("value", ""))
        feature_key = body.get("feature", "")
        flags_raw = body.get("flags")
        if not tenant:
            return jsonify({"ok": False, "error": "tenant is required"}), 400
        if value not in ("1", "0"):
            return jsonify({"ok": False, "error": "value must be '1' or '0'"}), 400
        action = "Enable" if value == "1" else "Disable"
        try:
            flags = _resolve_flags(flags_raw, feature_key)
            result = _set(stack, tenant, value, flags)
            result["action"] = action
            result["feature"] = feature_key or "custom"
            return jsonify(result)
        except ProvisionerError as exc:
            return jsonify(
                {"ok": False, "action": action, "error": exc.message, "output": exc.output}
            ), 200
        except subprocess.TimeoutExpired:
            return jsonify({"ok": False, "action": action, "error": "operation timed out"}), 200
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "action": action, "error": str(exc)}), 200

    return bp
