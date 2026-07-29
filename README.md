# Virtual Private Edge (VPE) - Utility Belt

> **Netskope proprietary — RiskInsights team.**
> A multi-utility internal web dashboard. Built as a React single-page app + a
> Python/Flask backend, deployed on a Teleport appliance and served over the
> appliance's floating IP.

Live URL (internal): `http://10.111.8.66:5001/`
Remote host: `tsh ssh --cluster iad0 smirza@ri-rca-dashboard.appliance.nc4.iad0.nsscloud.net`

---

## Utilities

The left sidebar switches between utilities. Each utility owns its own data fetching;
adding a new utility is a one-line entry in the view registry (see
[Extending](#extending)).

### 1. Stack Version Dashboard 🗂️
Per-Rancher-cluster view of the running **pod images** and the **internal packages**
shipped in the `artifactservice` pod, across all stacks in `~/rancher/*.yaml`.

- Columns: Stack, Env, Platform (vpe-sf), KVM, OVA, SWG, AIS, SAID, Content, GeoIP DB, Pod Images.
- **Pod Images** column shows one running replica per service with a green dot; a red dot
  (with status tooltip: `Running`, `CrashLoopBackOff`, `Terminating`, …) appears only when
  a service has **no** running replica. Gathers `artifactservice`, `artifactsync`,
  `vpe-manager`, `callhome`, `alarmmanager`, and `cloudmetricsgenerator` pods.
- Compact/Full toggle (click the title): compact strips prefixes/suffixes
  (e.g. `NSKVM-1.1.36.zip` → `1.1.36`); full shows the raw strings.
- All-tab ordering: prod stacks first, NPE (QA01/STG01/DEVINT/NPE02/FED1MP/PERF01) last.
- Search across stack names + version strings (with match highlighting).
- **Snapshot** button copies the full table (all columns/rows, including off-screen) to the
  clipboard as a PNG.
- Auto-refresh every 5 min (silent — no UI flicker); manual **Refresh** shows a
  "Gathering data…" spinner overlay.
- 19 stacks (13 prod + 6 npe). See [Clusters](#clusters).

### 2. Provisioning ⚙️
Enable / disable feature-flag groups for a tenant on a chosen stack by `kubectl exec`-ing
into the cluster's `callhomeservice-callhome` pod and curl-ing the in-cluster
`provisioner-pycore` service from inside the pod.

- Features (defined in `backend/provisioner.py` `FEATURES`):
  - **VPE Beta** — `nplan5663_vpe_setting_enabled`
  - **AI Guardrails** — `nplan5283_ai_security`, `nplan5663_vpe_setting_enabled`, `nplan6445_aiguardrails_vpe`
  - **Custom** — type any comma-separated flag names.
- Confirm dialog before any Enable/Disable (state-changing). No auth (internal tool).
- Per-flag set results + verification (re-read after write); raw failure output shown on error.

### 3. Tenant ID Finder 🔍
Look up a tenant ID by org name or domain substring on a chosen stack by `kubectl exec`-ing
into the cluster's `provisioner-core` pod (namespace `…--provisioner-core--provisioner-tm`)
and curl-ing `http://provisioner-core/org/list` from inside the pod.

- Case-insensitive substring search across `ui_hostname`, `name`, `description`, `dbname`, `TenantID`.
- Results table with a copy-to-clipboard button per tenant ID.
- Read-only.

### 4. VPE Tethering Diagnosis 🩺
Run a read-only tethering diagnostic against a VPE to pinpoint the exact failed lifecycle
stage. SSHes into the VPE (`sshpass`, user `nsadmin` / password `nsappliance` by default —
both overridable in the UI, left blank to use defaults), runs a read-only collector on the
box (`status.json`, on-box config, pod state/events, cert, logs), and classifies the
tethering scenario.

- Backend: `POST /api/vpe-diag/run` `{ip, user?, password?}` → runs
  `python3 vpe_tether_diag.py <ip> --json` and returns a structured report. No credentials
  are stored client-side; the password input is masked and the default value is never shown
  in plain text.
- The report renders as a **structured checklist table** grouped by stage
  (Pre-flight → Stage 0…5), one row per expected state with a ✓ / ✗ / ⚠ mark. The first
  failure is tagged `ROOT CAUSE`; downstream failures are tagged `blocked upstream`.
- **Click any row to expand** it and see: what the check validates, the on-box file/source
  queried, and the exact command executed on the VPE (run inside the read-only
  `sudo -n python3 -` collector over SSH — no mutating actions). Click again to collapse.
- Header strip with status badge (SUCCESS / FAILING / IN PROGRESS / FRESH / DEPROVISIONED /
  UNKNOWN), enrollment age (`Xhr Ymin ago`), identity chips (serial / tenant / TID /
  identifier), and ✓/✗/⚠ count tiles. A raw `status tethering` card (byte-identical to
  `/opt/ns/appliance/status.json .tethering_status`) renders below the table.
- **Copy report** copies the full JSON report.
- Internal scenario codes (S1/S2/S3/S4) are never shown in the UI — only human-readable
  labels and the status badge.

---

## Architecture

```
Browser ──HTTP──▶ Flask (port 5001, 0.0.0.0)
                   ├── serves built React bundle (dist/)
                   ├── /api/data, /api/refresh        (Stack Version Dashboard)
                   ├── /api/provisioner/*             (Provisioning)
                   ├── /api/tenant-finder/*           (Tenant ID Finder)
                   ├── /api/vpe-diag/run              (VPE Tethering Diagnosis)
                   └── /api/stacks                    (shared cluster list)
                            │
                            ▼
                   bash stacks_build_version.sh --json   (kubectl against ~/rancher/*.yaml)
                   kubectl exec into callhomeservice / provisioner-core pods
                   sshpass → VPE (read-only collector) for tethering diagnosis
```

- **Frontend** (`frontend/`): Vite + React + TypeScript. Built locally, the static `dist/`
  is shipped to the appliance. No UI library — hand-written CSS. `html-to-image` is the only
  non-React runtime dep (for the Snapshot feature).
- **Backend** (`backend/`): Flask (Python 3.8+, stdlib + Flask only — no pip installs needed
  on the appliance). Serves `dist/` and the JSON API. Runs as a systemd service.
- **Data source** (`script/stacks_build_version.sh`): the single source of truth for the
  Stack Version Dashboard. Enumerates `~/rancher/*.yaml` kubeconfigs, gathers pod images
  (with status) and internal packages via `kubectl`/`kubectl exec`, and emits JSON via
  `--json` (jq). The same script also prints a text matrix when run without `--json`.
- **Tethering diagnostic** (`backend/vpe_tether_diag.py`): the read-only diagnostic script
  (SSH + on-box collector + scenario classification). `backend/vpe_diag.py` is the Flask
  blueprint that invokes it with `--json` and returns the structured report.

The appliance has no Node.js, so the React app is built on a developer machine and the
static bundle is shipped to the appliance. The appliance has `kubectl` at
`~/.local/bin/kubectl`, `sshpass` at `/usr/bin/sshpass`, `jq` at `/usr/bin/jq`, and
Python 3.8 + Flask.

---

## Clusters

Stacks are enumerated from the kubeconfigs in `~/rancher/*.yaml`. Display names and env
classification are configured in `backend/app.py` (`DISPLAY_NAMES`, `NPE_RE`).

- **Prod (13)**: SV5, AM2, FR4, SJC1, BOM3, DFW3, FRA2, LON3, MEL2, RUH1, SIN2, SJC2, ZUR2.
- **NPE (6)**: STG01, QA01, DEVINT, NPE02, FED1MP, PERF01.

The three newest NPE clusters (NPE02 `c-wfc98`, FED1MP `c-czc66`, PERF01 `c-rsnsj`) live on
`rancher.prime.iad0.netskope.com`. Their kubeconfigs are generated via the Rancher API
(`POST /v3/clusters/<id>?action=generateKubeconfig`) and refreshed daily by
`backend/fetch_kubeconfigs.sh` (cron `17 3 * * *`), which reads a Rancher API token from
`~/.rancher_prime_token` (mode 600, git-ignored) and writes the kubeconfigs to `~/rancher/`.
Add more clusters by extending the `CLUSTERS` array in that script (`cluster-id|filename.yaml`).

> **STG01** may be absent from the dashboard when its `c-mvvwx` k8s proxy hangs from the
> appliance (the `cattle-cluster-agent` tunnel is down). The other clusters on the same
> Rancher work fine, so this is a STG01-side issue. Fix: restart the cluster agent on STG01,
> or give the appliance a route to its direct API server.

---

## Repository layout

```
ri-utility-belt/
├── README.md
├── .gitignore
├── backend/
│   ├── app.py                  # Flask app: serves dist/, stack-version API, scheduler
│   ├── provisioner.py          # Provisioning blueprint (kubectl-exec into callhomeservice)
│   ├── tenant_finder.py        # Tenant ID Finder blueprint (kubectl-exec into provisioner-core)
│   ├── vpe_diag.py             # VPE Tethering Diagnosis blueprint (runs vpe_tether_diag.py --json)
│   ├── vpe_tether_diag.py      # read-only tethering diagnostic script (SSH + on-box collector)
│   ├── fetch_kubeconfigs.sh    # periodic Rancher kubeconfig refresher (cron-run)
│   └── rca-dashboard.service   # systemd unit
├── frontend/
│   ├── index.html
│   ├── package.json
│   ├── tsconfig.json
│   ├── vite.config.ts
│   ├── public/favicon.svg
│   └── src/
│       ├── main.tsx, App.tsx, App.css, index.css, types.ts, api.ts
│       ├── components/  (Sidebar, Tabs, RefreshButton, SearchBar, SnapshotButton, StatusLabels, DataTable)
│       └── views/       (StackVersionDashboard, Provisioning, TenantFinder, VpeTetherDiag)
└── script/
    └── stacks_build_version.sh   # per-stack image/package gatherer (--json for the dashboard)
```

---

## Local development

### Frontend
```bash
cd frontend
npm install
npm run dev        # Vite dev server (proxies /api to http://localhost:5001)
npm run build      # type-check + production build → dist/
```

### Backend
```bash
cd backend
python3 -m venv .venv && source .venv/bin/activate
pip install flask
python app.py      # serves dist/ + API on 0.0.0.0:5001
```
Env overrides: `PORT`, `RANCHER_DIR` (default `~/rancher`), `STACKS_SCRIPT`
(default `~/rca-dashboard/stacks_build_version.sh`), `DIST_DIR` (default `./dist`),
`REFRESH_INTERVAL` (seconds, default 300), `RUN_TIMEOUT` (default 180).
The tethering script also honors `VPE_SSH_USER` / `VPE_SSH_PASS` for its sshpass defaults.

> The backend only functions where it can reach the clusters via the kubeconfigs in
> `RANCHER_DIR` and SSH to VPEs — i.e. on the appliance (or a host with equivalent access).

---

## Build & deploy (to the appliance)

The remote app lives at `~/rca-dashboard/` with this layout:
```
~/rca-dashboard/
├── app.py  provisioner.py  tenant_finder.py  vpe_diag.py  vpe_tether_diag.py
├── fetch_kubeconfigs.sh  rca-dashboard.service  (also installed to /etc/systemd/system/)
├── stacks_build_version.sh
└── dist/   (built frontend)
```

From a developer machine (with `tsh`, `rsync`, Node):

```bash
REMOTE="smirza@ri-rca-dashboard.appliance.nc4.iad0.nsscloud.net"

# 1. Build the frontend
cd frontend && npm run build && cd ..

# 2. Ship the backend + script
rsync -avz -e "tsh ssh --cluster iad0" \
  backend/app.py backend/provisioner.py backend/tenant_finder.py \
  backend/vpe_diag.py backend/vpe_tether_diag.py backend/fetch_kubeconfigs.sh \
  "$REMOTE:~/rca-dashboard/"

rsync -avz -e "tsh ssh --cluster iad0" \
  script/stacks_build_version.sh "$REMOTE:~/rca-dashboard/stacks_build_version.sh"

# 3. Ship the built frontend
rsync -avz -e "tsh ssh --cluster iad0" --delete \
  frontend/dist/ "$REMOTE:~/rca-dashboard/dist/"

# 4. Restart the service
tsh ssh --cluster iad0 "$REMOTE" \
  'sudo systemctl restart rca-dashboard.service && sudo systemctl is-active rca-dashboard.service'
```

First-time install: copy `backend/rca-dashboard.service` to `/etc/systemd/system/`, then
`sudo systemctl daemon-reload && sudo systemctl enable --now rca-dashboard.service`. For the
NPE kubeconfig refresh, put a Rancher API token in `~/.rancher_prime_token` (mode 600) and
install the cron entry from `backend/fetch_kubeconfigs.sh` (e.g. `17 3 * * *`).

Logs: `sudo journalctl -u rca-dashboard.service -f` (on the appliance).

---

## Configuration

- **`DISPLAY_NAMES`** (`backend/app.py`): maps kubeconfig filenames to short uppercase
  display names shown in the UI (e.g. `stork-lon3-…-nc1.yaml` → `LON3`).
- **Env classification** (`NPE_RE` in `backend/app.py`, `is_npe()` in the script): a stack
  is `npe` if its filename matches `qa01|stg01|devint|npe02|fed1mp|perf01`, else `prod`.
- **`FEATURES`** (`backend/provisioner.py`): the feature-flag groups for the Provisioning
  utility. Add a feature by appending to this list — the frontend reads
  `/api/provisioner/features` and picks it up automatically.
- **Column strip rules** (`frontend/src/components/DataTable.tsx` `STRIP`): per-column
  prefix/suffix to strip in compact view.
- **Tethering check metadata** (`CHECK_META` in `backend/vpe_tether_diag.py`): per-check
  `what`/`sources`/`commands` shown in the expandable rows. The `--json` report is built by
  `build_json_report()`; the `evaluate_checks()` helper is shared with the text renderer.

---

## Extending (adding a utility)

1. Create `frontend/src/views/MyUtility.tsx`.
2. Register it in `frontend/src/App.tsx`:
   ```ts
   const VIEWS: ViewDef[] = [
     { key: 'stack-versions', label: 'Stack Version Dashboard', icon: '🗂️' },
     { key: 'provisioning', label: 'Provisioning', icon: '⚙️' },
     { key: 'tenant-finder', label: 'Tenant ID Finder', icon: '🔍' },
     { key: 'vpe-tether-diag', label: 'VPE Tethering Diagnosis', icon: '🩺' },
     { key: 'my-utility', label: 'My Utility', icon: '🧰' },   // add
   ]
   const VIEW_COMPONENTS: Record<string, () => JSX.Element> = { …, 'my-utility': MyUtility }
   ```
3. If it needs a backend, add a Flask blueprint (see `provisioner.py` / `tenant_finder.py` /
   `vpe_diag.py`) and register it in `app.py`.

The sidebar picks up the new entry automatically.

---

## Notes

- **No secrets in this repo.** Kubeconfigs, API tokens, and certs live on the appliance
  (`~/rancher/*.yaml`, `~/.rancher_prime_token`, `~/.rancher_*_api_token.cfg`) and are
  git-ignored. Never commit them.
- The Stack Version Dashboard's data shape (`images: [{image, running, status, pods}]`,
  `packages: {category: [files]}`) is produced by `script/stacks_build_version.sh --json`.
- The Provisioning and Tenant ID Finder utilities reach the in-cluster services by
  `kubectl exec`-ing into a pod and curl-ing from inside (the short service names don't
  resolve from the appliance directly).
- The VPE Tethering Diagnosis script uses password-based SSH (`sshpass` with
  `IdentitiesOnly=yes` + `PreferredAuthentications=password` + `PubkeyAuthentication=no` so
  an agent full of keys doesn't trigger "Too many authentication failures"). It is
  strictly read-only — no mutating actions on the VPE.
- Auto-refresh is backend-driven (every 5 min) and silent; only an explicit Refresh click
  shows the "Gathering data…" overlay.
