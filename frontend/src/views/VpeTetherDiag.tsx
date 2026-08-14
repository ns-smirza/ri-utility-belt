import { Fragment, useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { runVpeDiag, type DiagCheckRow, type DiagMark, type VpeDiagResult } from '../api'

/** Copy text with an execCommand fallback for non-secure (HTTP) contexts. */
async function copyText(text: string): Promise<boolean> {
  try {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text)
      return true
    }
  } catch {
    /* fall through to execCommand */
  }
  try {
    const ta = document.createElement('textarea')
    ta.value = text
    ta.setAttribute('readonly', '')
    ta.style.position = 'fixed'
    ta.style.top = '0'
    ta.style.left = '0'
    ta.style.opacity = '0'
    document.body.appendChild(ta)
    ta.focus()
    ta.select()
    const ok = document.execCommand('copy')
    document.body.removeChild(ta)
    return ok
  } catch {
    return false
  }
}

// Very loose IP/hostname check — the backend does the authoritative validation.
const IP_RE = /^(?:(?:\d{1,3}\.){3}\d{1,3}|[A-Za-z0-9](?:[A-Za-z0-9.-]{0,253}[A-Za-z0-9])?)$/

const STAGE_LABELS: Record<string, string> = {
  PRE: 'Pre-flight',
  '0': 'Stage 0 — Registration key & certificate enrollment',
  '1': 'Stage 1 — cfgagent WebSocket → configdist',
  '2': 'Stage 2 — configdist config push & serial assignment',
  '3': 'Stage 3 — callhome reachability',
  '4': 'Stage 4 — Tethering status all-true',
  '5': 'Stage 5 — Operational (metrics / heartbeat)',
}

const STATUS_LABELS: Record<string, string> = {
  SUCCESS: 'SUCCESS',
  SUCCESS_RETHETHER: 'SUCCESS (re-tether)',
  COMPLETE_OP_PENDING: 'COMPLETE — OP PENDING',
  SUCCESS_WITH_FAILURES: 'SUCCESS W/ FAILURES',
  FRESH: 'FRESH',
  DEPROVISIONED: 'DEPROVISIONED',
  IN_PROGRESS: 'IN PROGRESS',
  FAILING: 'FAILING',
  UNKNOWN: 'UNKNOWN',
}

const STATUS_TONE: Record<string, string> = {
  SUCCESS: 'ok',
  SUCCESS_RETHETHER: 'ok',
  COMPLETE_OP_PENDING: 'warn',
  SUCCESS_WITH_FAILURES: 'warn',
  FRESH: 'idle',
  DEPROVISIONED: 'idle',
  IN_PROGRESS: 'warn',
  FAILING: 'fail',
  UNKNOWN: 'warn',
}

const MARK_SYMBOL: Record<DiagMark, string> = { tick: '✓', cross: '✗', warn: '⚠', na: '·' }
const MARK_TONE: Record<DiagMark, string> = { tick: 'ok', cross: 'fail', warn: 'warn', na: 'idle' }

// Human-readable title for the scenario-confirmation section (keyed on the
// internal scenario code, never displayed as a raw code).
const CONFIRM_TITLE: Record<string, string> = {
  S1: 'Fresh-box confirmation markers',
  S3: 'Deprovisioned stale-marker confirmation',
  S4: 'Re-tethered history-marker confirmation',
}

/** Format an enrollment age (minutes) as "Xhr Ymin ago" / "Ymin ago". */
function formatAge(min: number | null): string {
  if (min == null) return 'unknown'
  const h = Math.floor(min / 60)
  const m = min % 60
  if (h >= 1) return `${h}hr ${m}min ago`
  return `${m}min ago`
}

/** Render text with bare URLs as clickable links (opens in a new tab). */
function linkify(text: string): ReactNode {
  const parts = text.split(/(https?:\/\/[^\s]+)/)
  return parts.map((p, i) =>
    /^https?:\/\//.test(p) ? (
      <a key={i} href={p} target="_blank" rel="noopener noreferrer">
        {p}
      </a>
    ) : (
      <Fragment key={i}>{p}</Fragment>
    ),
  )
}

/** Group ordered checks by stage preserving the script's chronological order. */
function groupByStage(rows: DiagCheckRow[]): { stage: string; rows: DiagCheckRow[] }[] {
  const groups: { stage: string; rows: DiagCheckRow[] }[] = []
  for (const r of rows) {
    const last = groups[groups.length - 1]
    if (last && last.stage === r.stage) last.rows.push(r)
    else groups.push({ stage: r.stage, rows: [r] })
  }
  return groups
}

export function VpeTetherDiag() {
  const [ip, setIp] = useState('')
  const [user, setUser] = useState('')
  const [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false)
  const [elapsed, setElapsed] = useState(0)
  const [result, setResult] = useState<VpeDiagResult | null>(null)
  const [copied, setCopied] = useState(false)
  const [statusCopied, setStatusCopied] = useState(false)
  const [expanded, setExpanded] = useState<string | null>(null)
  const startRef = useRef(0)

  const cleanedIp = ip.trim()
  const ready = IP_RE.test(cleanedIp)

  useEffect(() => {
    if (!busy) return
    const id = window.setInterval(() => {
      setElapsed(Math.floor((Date.now() - startRef.current) / 1000))
    }, 500)
    return () => window.clearInterval(id)
  }, [busy])

  const onDiagnose = useCallback(async () => {
    if (!ready || busy) return
    setBusy(true)
    setElapsed(0)
    startRef.current = Date.now()
    setResult(null)
    setExpanded(null)
    try {
      setResult(await runVpeDiag(cleanedIp, user.trim() || undefined, password || undefined))
    } catch (e) {
      setResult({ ok: false, error: String(e) })
    } finally {
      setBusy(false)
    }
  }, [ready, busy, cleanedIp])

  const toggleRow = useCallback((key: string) => {
    setExpanded((cur) => (cur === key ? null : key))
  }, [])

  const copyReport = useCallback(async () => {
    if (!result?.report) return
    const ok = await copyText(JSON.stringify(result.report, null, 2))
    if (ok) {
      setCopied(true)
      window.setTimeout(() => setCopied(false), 1500)
    }
  }, [result])

  const copyStatus = useCallback(async () => {
    if (!result?.report) return
    const blob = `Tethering status
${JSON.stringify(result.report.tetheringStatus, null, 2)}
${Object.keys(result.report.reachabilityStatus || {}).length > 0 ? `\nreachability_status\n${JSON.stringify(result.report.reachabilityStatus, null, 2)}` : ''}`
    const ok = await copyText(blob)
    if (ok) {
      setStatusCopied(true)
      window.setTimeout(() => setStatusCopied(false), 1500)
    }
  }, [result])

  const report = result?.ok ? result.report : undefined
  const statusTone = report ? STATUS_TONE[report.status] ?? 'warn' : 'warn'
  const statusLabel = report ? STATUS_LABELS[report.status] ?? report.status : ''
  const criticalGroups = useMemo(() => (report ? groupByStage(report.checks) : []), [report])
  const failGroup = report?.firstFailStage
    ? criticalGroups.find((g) => g.stage === report.firstFailStage)
    : undefined

  return (
    <div className="view vpe-tether-diag" data-testid="vpe-tether-diag-root">
      <header className="view-header" data-testid="header">
        <div className="title-block">
          <h1 data-testid="title">VPE Tethering Diagnosis</h1>
          <p className="subtitle">
            Run the read-only tethering diagnostic against a VPE to pinpoint the exact failed
            lifecycle stage
          </p>
        </div>
      </header>

      <div className="prov-card" data-testid="vpe-diag-card">
        <div className="prov-form">
          <label className="prov-field prov-search-field">
            <span className="prov-label">VPE IP</span>
            <input
              className="prov-input"
              data-testid="vpe-diag-ip-input"
              type="text"
              placeholder="e.g. 10.111.6.92"
              value={ip}
              onChange={(e) => setIp(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') onDiagnose()
              }}
            />
          </label>

          <div className="prov-actions">
            <button
              className="prov-btn"
              data-testid="vpe-diag-run-button"
              onClick={onDiagnose}
              disabled={!ready || busy}
            >
              {busy ? 'Diagnosing…' : 'Diagnose'}
            </button>
          </div>
        </div>

        <div className="prov-form diag-creds-row">
          <label className="prov-field">
            <span className="prov-label">SSH user</span>
            <input
              className="prov-input"
              data-testid="vpe-diag-user-input"
              type="text"
              placeholder="nsadmin"
              value={user}
              onChange={(e) => setUser(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') onDiagnose()
              }}
            />
          </label>
          <label className="prov-field">
            <span className="prov-label">SSH password</span>
            <input
              className="prov-input"
              data-testid="vpe-diag-pass-input"
              type="password"
              placeholder="••••••••"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') onDiagnose()
              }}
            />
          </label>
          <p className="diag-creds-hint">
            Leave blank to use the default credentials.
          </p>
        </div>

        <p className="prov-context" data-testid="vpe-diag-context">
          SSHes into the VPE as <code>{user.trim() || 'nsadmin'}</code> and runs a read-only collector (status.json,
          config, pod state/events, cert, logs). Classifies the scenario and lists each expected
          stage with a tick / cross. <strong>No mutating actions.</strong> Collection can take up
          to ~90s.
        </p>
      </div>

      {(result || busy) && (
        <>
        <div className="prov-result-card" data-testid="vpe-diag-result">
          {busy && (
            <p className="prov-loading" data-testid="vpe-diag-loading">
              <span className="spinner spinner-light" aria-hidden="true" /> Diagnosing {cleanedIp}…
              {' '}({elapsed}s) — SSHing to the VPE and collecting on-box state; this can take up to ~90s.
            </p>
          )}

          {!busy && result && !result.ok && (
            <div className="prov-fail" data-testid="vpe-diag-error">
              <p className="prov-error" data-testid="vpe-diag-error-output">
                {result.error || 'Diagnosis failed'}
              </p>
              {result.output && (
                <pre className="prov-output" data-testid="vpe-diag-error-report">
                  {result.output}
                </pre>
              )}
            </div>
          )}

          {!busy && report && (
            <div className="diag-report-wrap" data-testid="vpe-diag-report">
              {/* Header strip */}
              <div className="diag-head">
                <div className="diag-head-meta">
                  <div className="diag-hostline">
                    <strong>{report.ip}</strong>
                    <span className="diag-hostname">{report.hostname}</span>
                    {report.build && <span className="diag-build">build {report.build}</span>}
                  </div>
                  <div className="diag-subline">
                    <span>Captured {report.captured}</span>
                    {report.ageMin != null && <span>enrolled {formatAge(report.ageMin)}</span>}
                    {report.durationSec != null && <span>{report.durationSec}s</span>}
                  </div>
                </div>
                <div className="diag-head-badges">
                  <span className={`diag-status diag-status-${statusTone}`} data-testid="vpe-diag-status-badge">
                    {statusLabel}
                  </span>
                  <button
                    className={`tf-copy-icon${copied ? ' copied' : ''}`}
                    data-testid="vpe-diag-copy"
                    title={copied ? 'Copied!' : 'Copy report'}
                    aria-label="Copy report"
                    onClick={copyReport}
                  >
                    {copied ? (
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                        <polyline points="20 6 9 17 4 12" />
                      </svg>
                    ) : (
                      <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                        <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                        <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                      </svg>
                    )}
                  </button>
                </div>
              </div>

              {/* Summary banner */}
              <div className={`diag-summary diag-summary-${statusTone}`} data-testid="vpe-diag-summary">
                <div className="diag-summary-msg">{report.summaryMessage}</div>
                {report.likelyCause && (
                  <div className="diag-cause" data-testid="vpe-diag-cause">
                    <span className="diag-cause-label">Likely cause</span>
                    {linkify(report.likelyCause)}
                  </div>
                )}
              </div>

              {/* Identity chips + count tiles */}
              <div className="diag-meta-row">
                <div className="diag-identity" data-testid="vpe-diag-identity">
                  {report.identity.serial && <Chip label="Serial" value={report.identity.serial} />}
                  {report.identity.tenantUrl && <Chip label="Tenant" value={report.identity.tenantUrl} />}
                  {report.identity.tenantId && <Chip label="TID" value={report.identity.tenantId} />}
                  {report.identity.identifier && <Chip label="Identifier" value={report.identity.identifier} />}
                </div>
                <div className="diag-counts" data-testid="vpe-diag-counts">
                  <CountTile tone="ok" sym="✓" n={report.counts.ticks} label="achieved" />
                  <CountTile tone="fail" sym="✗" n={report.counts.crosses} label="failed" />
                  <CountTile tone="warn" sym="⚠" n={report.counts.warns} label="ignorable" />
                  {report.counts.na > 0 && (
                    <CountTile tone="idle" sym="·" n={report.counts.na} label="n/a" />
                  )}
                </div>
              </div>

              {/* Checklist table */}
              <table className="diag-table" data-testid="vpe-diag-table">
                <thead>
                  <tr>
                    <th className="col-status">Status</th>
                    <th className="col-cid">Check</th>
                    <th className="col-label">Description</th>
                    <th className="col-reason">Details</th>
                  </tr>
                </thead>
                <tbody>
                  {criticalGroups.map((g) => (
                    <StageGroup
                      key={g.stage}
                      stage={g.stage}
                      rows={g.rows}
                      expanded={expanded}
                      onToggle={toggleRow}
                    />
                  ))}
                  {report.ignorableChecks.length > 0 && (
                    <StageGroup
                      stage="__ign"
                      rows={report.ignorableChecks}
                      ignorable
                      expanded={expanded}
                      onToggle={toggleRow}
                    />
                  )}
                </tbody>
              </table>

              {/* Confirmation markers */}
              {report.confirmation.length > 0 && (
                <div className="diag-confirmation" data-testid="vpe-diag-confirmation">
                  <h3 className="diag-section-title">
                    {CONFIRM_TITLE[report.scenario] ?? 'Confirmation markers'}
                  </h3>
                  <ul className="diag-confirm-list">
                    {report.confirmation.map((c, i) => (
                      <li key={i} className={`diag-confirm-item diag-${c.ok ? 'ok' : 'fail'}`}>
                        <span className="diag-confirm-mark">{c.ok ? '✓' : '✗'}</span>
                        <span>{c.label}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {report.staleFiltered > 0 && report.cycleAnchor && (
                <p className="diag-footnote" data-testid="vpe-diag-stale-note">
                  Cycle-aware: ignored {report.staleFiltered} stale log line(s) from prior tether
                  cycles; anchored to current enrollment {report.cycleAnchor}.
                </p>
              )}
              {failGroup && (
                <p className="diag-footnote diag-footnote-fail">
                  First failure at {STAGE_LABELS[report.firstFailStage!] ?? report.firstFailStage}.
                </p>
              )}
            </div>
          )}
        </div>

        {!busy && report && (
          <div className="prov-result-card diag-status-card" data-testid="vpe-diag-status-card">
            <div className="diag-status-head">
              <h2 className="prov-result-title">
                <code>status tethering</code>
              </h2>
              <span className="diag-status-sub">
                nsshell — byte-identical to /opt/ns/appliance/status.json .tethering_status
              </span>
              <button
                className={`tf-copy-icon${statusCopied ? ' copied' : ''}`}
                data-testid="vpe-diag-status-copy"
                title={statusCopied ? 'Copied!' : 'Copy status'}
                aria-label="Copy status"
                onClick={copyStatus}
              >
                {statusCopied ? (
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <polyline points="20 6 9 17 4 12" />
                  </svg>
                ) : (
                  <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                    <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                  </svg>
                )}
              </button>
            </div>
            <pre className="diag-status-pre" data-testid="vpe-diag-tethering-status">
{`Tethering status
${JSON.stringify(report.tetheringStatus, null, 2)}`}
            </pre>
            {Object.keys(report.reachabilityStatus || {}).length > 0 && (
              <>
                <h3 className="diag-status-subhead">
                  <code>reachability_status</code>
                </h3>
                <pre className="diag-status-pre" data-testid="vpe-diag-reachability-status">
{JSON.stringify(report.reachabilityStatus, null, 2)}
                </pre>
              </>
            )}
          </div>
        )}

        {!busy && report && (
          <div className="prov-result-card diag-regtoken-card" data-testid="vpe-diag-regtoken-card">
            <div className="diag-status-head">
              <h2 className="prov-result-title">
                <code>registration token</code>
              </h2>
              <span className="diag-status-sub">
                JWT applied via <code>set system registrationkey</code> — decoded (no signature verification)
              </span>
            </div>

            {report.registrationToken.jwtPresent ? (
              <>
                <div className="diag-regtoken-fields" data-testid="vpe-diag-regtoken-fields">
                  <Chip label="Device ID" value={String(report.registrationToken.did || '—')} />
                  <Chip label="Tenant ID" value={String(report.registrationToken.tid ?? '—')} />
                  <Chip label="FQDN / env" value={report.registrationToken.fqdn || '—'} />
                  <Chip label="Issued" value={report.registrationToken.iatDate || '—'} />
                  <span
                    className={`diag-regtoken-expiry ${report.registrationToken.expired ? 'exp-expired' : 'exp-valid'}`}
                    data-testid="vpe-diag-regtoken-expiry"
                  >
                    <span className="diag-chip-label">Expires</span>
                    <code>{report.registrationToken.expDate || '—'}</code>
                    <span className="diag-expiry-tag">
                      {report.registrationToken.expired == null
                        ? '?'
                        : report.registrationToken.expired
                          ? 'EXPIRED'
                          : 'valid'}
                    </span>
                  </span>
                </div>
                <h3 className="diag-status-subhead">
                  <code>decoded payload</code>
                </h3>
                <pre className="diag-status-pre" data-testid="vpe-diag-regtoken-payload">
{JSON.stringify(report.registrationToken.payload, null, 2)}
                </pre>
              </>
            ) : (
              <p className="prov-loading">
                No registrationkey JWT found in config.json — no registration key has been applied (or
                config.json is unreadable).
              </p>
            )}

            <h3 className="diag-status-subhead">
              <code>registration_token.json (on-box)</code>
            </h3>
            {report.registrationToken.tokenFile.present ? (
              <pre className="diag-status-pre" data-testid="vpe-diag-regtoken-file">
{JSON.stringify(report.registrationToken.tokenFile, null, 2)}
              </pre>
            ) : (
              <p className="prov-loading">
                registration_token.json not on disk — enrollment did not persist it (expected on a
                fresh box or when enrollment failed before the save completed).
              </p>
            )}
          </div>
        )}

        {!busy && report && (
          <div className="prov-result-card diag-podsall-card" data-testid="vpe-diag-podsall-card">
            <div className="diag-status-head">
              <h2 className="prov-result-title">
                <code>kubectl get pods -A</code>
              </h2>
              <span className="diag-status-sub">all namespaces, verbatim on-box output</span>
            </div>
            <pre className="diag-status-pre diag-podsall-pre" data-testid="vpe-diag-podsall">
{report.podsAll || '(no output — pods -A returned nothing or SSH failed)'}
            </pre>
          </div>
        )}
        </>
      )}
    </div>
  )
}

function Chip({ label, value }: { label: string; value: string }) {
  return (
    <span className="diag-chip" title={value}>
      <span className="diag-chip-label">{label}</span>
      <code className="diag-chip-value">{value}</code>
    </span>
  )
}

function CountTile({ tone, sym, n, label }: { tone: string; sym: string; n: number; label: string }) {
  return (
    <span className={`diag-count diag-count-${tone}`} data-testid={`vpe-diag-count-${tone}`}>
      <span className="diag-count-sym">{sym}</span>
      <span className="diag-count-n">{n}</span>
      <span className="diag-count-label">{label}</span>
    </span>
  )
}

function StageGroup({
  stage,
  rows,
  ignorable,
  expanded,
  onToggle,
}: {
  stage: string
  rows: DiagCheckRow[]
  ignorable?: boolean
  expanded: string | null
  onToggle: (key: string) => void
}) {
  const title = ignorable ? 'Ignorable checks' : STAGE_LABELS[stage] ?? `Stage ${stage}`
  return (
    <>
      <tr className="diag-stage-row">
        <td colSpan={4}>{title}</td>
      </tr>
      {rows.map((r) => {
        const tone = MARK_TONE[r.mark]
        const key = `${r.stage}-${r.cid}`
        const isOpen = expanded === key
        return (
          <RowToggle
            key={key}
            row={r}
            tone={tone}
            open={isOpen}
            onToggle={() => onToggle(key)}
          />
        )
      })}
    </>
  )
}

function RowToggle({
  row: r,
  tone,
  open,
  onToggle,
}: {
  row: DiagCheckRow
  tone: string
  open: boolean
  onToggle: () => void
}) {
  return (
    <>
      <tr
        className={`diag-row diag-row-clickable diag-row-${tone}${r.firstFail ? ' diag-row-root' : ''}${r.blocked ? ' diag-row-blocked' : ''}${open ? ' diag-row-open' : ''}`}
        data-testid={`diag-row-${r.stage}-${r.cid}`}
        onClick={onToggle}
        aria-expanded={open}
        tabIndex={0}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault()
            onToggle()
          }
        }}
      >
        <td className="col-status">
          <span className={`diag-mark diag-mark-${tone}`} data-testid={`diag-mark-${r.stage}-${r.cid}`}>
            {MARK_SYMBOL[r.mark]}
          </span>
        </td>
        <td className="col-cid">
          <code>{r.cid}</code>
        </td>
        <td className="col-label">{r.label}</td>
        <td className="col-reason">
          <span className="diag-reason">{r.reason}</span>
          {r.firstFail && <span className="diag-tag diag-tag-root">ROOT CAUSE</span>}
          {r.blocked && <span className="diag-tag diag-tag-blocked">blocked upstream</span>}
          <span className={`diag-chevron${open ? ' diag-chevron-open' : ''}`} aria-hidden="true">
            ▸
          </span>
        </td>
      </tr>
      {open && (
        <tr className="diag-expand-row" data-testid={`diag-expand-${r.stage}-${r.cid}`}>
          <td colSpan={4}>
            <div className="diag-expand">
              <section className="diag-expand-section">
                <h4 className="diag-expand-h">What this check validates</h4>
                <p className="diag-expand-text">{r.what || 'No description available.'}</p>
              </section>
              <section className="diag-expand-section">
                <h4 className="diag-expand-h">File / source queried</h4>
                {r.sources.length > 0 ? (
                  <ul className="diag-expand-list diag-sources">
                    {r.sources.map((s, i) => (
                      <li key={i}>
                        <code>{s}</code>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="diag-expand-text">—</p>
                )}
              </section>
              <section className="diag-expand-section">
                <h4 className="diag-expand-h">Command executed on the VPE</h4>
                {r.commands.length > 0 ? (
                  <ul className="diag-expand-list diag-commands">
                    {r.commands.map((c, i) => (
                      <li key={i}>
                        <code>{c}</code>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <p className="diag-expand-text">—</p>
                )}
                <p className="diag-expand-note">
                  Run inside the read-only collector piped to <code>sudo -n python3 -</code> over SSH
                  (<code>sshpass -p •••• ssh nsadmin@&lt;ip&gt;</code>). No mutating actions.
                </p>
              </section>
            </div>
          </td>
        </tr>
      )}
    </>
  )
}
