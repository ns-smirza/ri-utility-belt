"""Tenant ID Finder utility backend (Flask Blueprint).

Finds a tenant ID for an org/domain on a given stack by exec-ing into the
cluster's provisioner-core pod (namespace `...--provisioner-core--provisioner-tm`)
and curl-ing the in-cluster `provisioner-core-provisioner-tm` service's
`/org/list` endpoint, then filtering the returned org list by the query.

Read-only. Registered by app.py via create_tenant_finder_bp(cfg); cfg provides
RANCHER_DIR and RUN_ENV.
"""

import json
import os
import subprocess

from flask import Blueprint, jsonify, request

KUBECTL_TIMEOUT = 60
CURL_MAX_TIME = "25"
ORG_LIST_URL = "http://provisioner-core/org/list"
NAMESPACE_GREP = "provisioner-core--provisioner-tm"
POD_GREP = "provisioner-core-provisioner-core"
RESULT_LIMIT = 50
SEARCH_FIELDS = ("ui_hostname", "name", "description", "dbname")


class TenantFinderError(Exception):
    def __init__(self, message, output=""):
        super().__init__(message)
        self.message = message
        self.output = output


def create_tenant_finder_bp(cfg):
    bp = Blueprint("tenant_finder", __name__)
    rancher_dir = cfg.RANCHER_DIR
    run_env = cfg.RUN_ENV

    def run(cmd):
        return subprocess.run(
            cmd, env=run_env, capture_output=True, text=True, timeout=KUBECTL_TIMEOUT
        )

    def _stack_path(stack):
        name = os.path.basename(stack)
        path = os.path.join(rancher_dir, name)
        if not name.endswith(".yaml") or not os.path.isfile(path):
            raise TenantFinderError("Unknown stack: " + stack)
        return name, path

    def _discover(stack):
        """Return a kubectl exec prefix for a running provisioner-core pod."""
        name, kc = _stack_path(stack)
        base = ["kubectl", "--kubeconfig", kc]

        p = run(base + ["get", "namespaces", "--no-headers"])
        if p.returncode != 0:
            raise TenantFinderError(
                "kubectl get namespaces failed for " + name,
                (p.stdout or "") + (p.stderr or ""),
            )
        ns = None
        for line in p.stdout.splitlines():
            parts = line.split()
            if parts and NAMESPACE_GREP in parts[0]:
                ns = parts[0]
                break
        if not ns:
            raise TenantFinderError(
                "No {} namespace found in {}".format(NAMESPACE_GREP, name), p.stdout
            )

        p = run(base + ["-n", ns, "get", "pods", "--no-headers"])
        if p.returncode != 0:
            raise TenantFinderError(
                "kubectl get pods failed in " + ns, (p.stdout or "") + (p.stderr or "")
            )
        pod = None
        for line in p.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 3 and POD_GREP in parts[0] and parts[2] == "Running":
                pod = parts[0]
                break
        if not pod:
            raise TenantFinderError(
                "No running {} pod in {}".format(POD_GREP, ns), p.stdout
            )

        p = run(
            base
            + ["-n", ns, "get", "pod", pod, "-o", "jsonpath={.spec.containers[0].name}"]
        )
        container = p.stdout.strip() or None

        exec_prefix = base + ["-n", ns, "exec", pod]
        if container:
            exec_prefix += ["-c", container]
        return exec_prefix

    def _extract_orgs(payload):
        if isinstance(payload, list):
            return payload
        if isinstance(payload, dict):
            for v in payload.values():
                if isinstance(v, list):
                    return v
        return []

    def _search(stack, query):
        exec_prefix = _discover(stack)
        p = run(
            exec_prefix
            + ["--", "curl", "-s", "--max-time", CURL_MAX_TIME, ORG_LIST_URL]
        )
        if p.returncode != 0:
            raise TenantFinderError(
                "curl /org/list failed inside pod", (p.stdout or "") + (p.stderr or "")
            )
        try:
            payload = json.loads(p.stdout)
        except ValueError:
            raise TenantFinderError("/org/list returned non-JSON response", p.stdout)

        orgs = _extract_orgs(payload)
        if not orgs:
            raise TenantFinderError(
                "/org/list returned no org entries", p.stdout[:1000]
            )

        needle = query.lower()
        matches = []
        total = 0
        for org in orgs:
            if not isinstance(org, dict):
                continue
            haystack_parts = [str(org.get(f, "")) for f in SEARCH_FIELDS]
            haystack_parts.append(str(org.get("TenantID", "")))
            haystack = " ".join(haystack_parts).lower()
            if needle in haystack:
                total += 1
                if len(matches) < RESULT_LIMIT:
                    matches.append(
                        {
                            "tenantId": org.get("TenantID"),
                            "name": org.get("name"),
                            "uiHostname": org.get("ui_hostname"),
                            "dbname": org.get("dbname"),
                            "description": org.get("description"),
                            "createTime": org.get("create_time"),
                        }
                    )
        return {
            "ok": True,
            "query": query,
            "count": total,
            "returned": len(matches),
            "truncated": total > len(matches),
            "matches": matches,
        }

    @bp.route("/api/tenant-finder/search", methods=["GET"])
    def search():
        stack = request.args.get("stack", "")
        query = (request.args.get("query", "") or "").strip()
        if not query:
            return jsonify({"ok": False, "error": "query is required"}), 400
        if len(query) < 2:
            return jsonify({"ok": False, "error": "enter at least 2 characters"}), 400
        try:
            return jsonify(_search(stack, query))
        except TenantFinderError as exc:
            return (
                jsonify({"ok": False, "error": exc.message, "output": exc.output}),
                200,
            )
        except subprocess.TimeoutExpired:
            return jsonify({"ok": False, "error": "operation timed out"}), 200
        except Exception as exc:  # noqa: BLE001
            return jsonify({"ok": False, "error": str(exc)}), 200

    return bp
