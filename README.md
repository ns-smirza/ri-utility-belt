# Risk Insights Utility Belt

> **Netskope proprietary — RiskInsights team.**
> A multi-utility internal web dashboard for the Risk Insights team. Built as a React
> single-page app + a Python/Flask backend, deployed on a Teleport appliance and served
> over the appliance's floating IP.

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

- Columns: Stack, Env, Software (vpe-sf), KVM, OVA, SWG, AIS, SAID, Content, GeoIP DB, Pod Images.
- **Pod Images** column shows one running replica per service with a green dot; a red dot
  (with status tooltip: `Running`, `CrashLoopBackOff`, `Terminating`, …) appears only when
  a service has **no** running replica.
- Compact/Full toggle (click the title): compact strips prefixes/suffixes
  (e.g. `NSKVM-1.1.36.zip` → `1.1.36`); full shows the raw strings.
- All-tab ordering: prod stacks first, NPE (QA01/STG01/DEVINT) last.
- Search across stack names + version strings (with match highlighting).
- **Snapshot** button copies the full table (all columns/rows, including off-screen) to the
  clipboard as a PNG.
- Auto-refresh every 5 min (silent — no UI flicker); manual **Refresh** shows a
  "Gathering data…" spinner overlay.

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

---

## Architecture

```
Browser ──HTTP──▶ Flask (port 5001, 0.0.0.0)
                   ├── serves built React bundle (dist/)
                   ├── /api/data, /api/refresh        (Stack Version Dashboard)
                   ├── /api/provisioner/*             (Provisioning)
                   ├── /api/tenant-finder/*           (Tenant ID Finder)
                   └── /api/stacks                    (shared cluster list)
                            │
                            ▼
                   bash stacks_build_version.sh --json   (kubectl against ~/rancher/*.yaml)
                   kubectl exec into callhomeservice / provisioner-core pods
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

The appliance has no Node.js, so the React app is built on a developer machine and the
static bundle is shipped to the appliance. The appliance has `kubectl` at
`~/.local/bin/kubectl`, `jq` at `/usr/bin/jq`, and Python 3.8 + Flask.

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
│       └── views/       (StackVersionDashboard, Provisioning, TenantFinder)
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

> The backend only functions where it can reach the clusters via the kubeconfigs in
> `RANCHER_DIR` — i.e. on the appliance (or a host with equivalent access).

---

## Build & deploy (to the appliance)

The remote app lives at `~/rca-dashboard/` with this layout:
```
~/rca-dashboard/
├── app.py  provisioner.py  tenant_finder.py
├── rca-dashboard.service  (also installed to /etc/systemd/system/)
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
`sudo systemctl daemon-reload && sudo systemctl enable --now rca-dashboard.service`.

Logs: `sudo journalctl -u rca-dashboard.service -f` (on the appliance).

---

## Configuration

- **`DISPLAY_NAMES`** (`backend/app.py`): maps kubeconfig filenames to short uppercase
  display names shown in the UI (e.g. `stork-lon3-…-nc1.yaml` → `LON3`).
- **Env classification**: a stack is `npe` if its filename matches `qa01|stg01|devint`,
  else `prod`.
- **`FEATURES`** (`backend/provisioner.py`): the feature-flag groups for the Provisioning
  utility. Add a feature by appending to this list — the frontend reads
  `/api/provisioner/features` and picks it up automatically.
- **Column strip rules** (`frontend/src/components/DataTable.tsx` `STRIP`): per-column
  prefix/suffix to strip in compact view.

---

## Extending (adding a utility)

1. Create `frontend/src/views/MyUtility.tsx`.
2. Register it in `frontend/src/App.tsx`:
   ```ts
   const VIEWS: ViewDef[] = [
     { key: 'stack-versions', label: 'Stack Version Dashboard', icon: '🗂️' },
     { key: 'provisioning', label: 'Provisioning', icon: '⚙️' },
     { key: 'tenant-finder', label: 'Tenant ID Finder', icon: '🔍' },
     { key: 'my-utility', label: 'My Utility', icon: '🧰' },   // add
   ]
   const VIEW_COMPONENTS: Record<string, () => JSX.Element> = { …, 'my-utility': MyUtility }
   ```
3. If it needs a backend, add a Flask blueprint (see `provisioner.py` / `tenant_finder.py`)
   and register it in `app.py`.

The sidebar picks up the new entry automatically.

---

## Notes

- **No secrets in this repo.** Kubeconfigs, API tokens, and certs live on the appliance
  (`~/rancher/*.yaml`, `~/.rancher_*_api_token.cfg`) and are git-ignored. Never commit them.
- The Stack Version Dashboard's data shape (`images: [{image, running, status, pods}]`,
  `packages: {category: [files]}`) is produced by `script/stacks_build_version.sh --json`.
- The Provisioning and Tenant ID Finder utilities reach the in-cluster services by
  `kubectl exec`-ing into a pod and curl-ing from inside (the short service names don't
  resolve from the appliance directly).
- Auto-refresh is backend-driven (every 5 min) and silent; only an explicit Refresh click
  shows the "Gathering data…" overlay.
