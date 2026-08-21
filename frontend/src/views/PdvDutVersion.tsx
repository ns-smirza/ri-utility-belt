import { useCallback, useEffect, useRef, useState } from 'react'
import { fetchPdvDut, refreshPdvDut, type PdvDutData, type PdvVersionRow } from '../api'
import { RefreshButton } from '../components/RefreshButton'

const POLL_MS = 15000
const AGO_TICK_MS = 15000

// version-info columns in display order (keys match the backend FIELDS)
const COLS: { key: string; label: string }[] = [
  { key: 'software', label: 'Software' },
  { key: 'content', label: 'Content' },
  { key: 'dpop', label: 'DPOP' },
  { key: 'oplp', label: 'OPLP' },
  { key: 'rollback', label: 'Rollback' },
  { key: 'threat-feed', label: 'Threat-feed' },
  { key: 'urldb', label: 'URLDB' },
]

function minutesAgo(iso: string | null, now: number): string {
  if (!iso) return 'never'
  const then = new Date(iso).getTime()
  if (Number.isNaN(then)) return 'never'
  const diffSec = Math.max(0, Math.floor((now - then) / 1000))
  if (diffSec < 60) return 'just now'
  const m = Math.floor(diffSec / 60)
  if (m < 60) return `${m} min ago`
  const h = Math.floor(m / 60)
  const rem = m % 60
  return `${h}h ${rem}m ago`
}

export function PdvDutVersion() {
  const [data, setData] = useState<PdvDutData | null>(null)
  const [now, setNow] = useState<number>(() => Date.now())
  const [manualRefresh, setManualRefresh] = useState(false)
  const sawRefreshingRef = useRef(false)
  const prevLastRefreshRef = useRef<string | null>(null)
  const manualTimeoutRef = useRef<number | null>(null)

  const poll = useCallback(async () => {
    try {
      const d = await fetchPdvDut()
      setData(d)
      if (d.refreshing) sawRefreshingRef.current = true
      if (manualRefresh) {
        const advanced = !!d.lastRefresh && d.lastRefresh !== prevLastRefreshRef.current
        if ((sawRefreshingRef.current && !d.refreshing) || advanced) {
          setManualRefresh(false)
        }
      }
    } catch (e) {
      console.error('pdv poll failed', e)
    }
  }, [manualRefresh])

  useEffect(() => {
    poll()
    const interval = manualRefresh ? 3000 : POLL_MS
    const id = window.setInterval(poll, interval)
    return () => window.clearInterval(id)
  }, [poll, manualRefresh])

  useEffect(() => {
    const id = window.setInterval(() => setNow(Date.now()), AGO_TICK_MS)
    return () => window.clearInterval(id)
  }, [])

  const onRefresh = useCallback(async () => {
    prevLastRefreshRef.current = data?.lastRefresh ?? null
    sawRefreshingRef.current = false
    setManualRefresh(true)
    if (manualTimeoutRef.current) window.clearTimeout(manualTimeoutRef.current)
    manualTimeoutRef.current = window.setTimeout(() => setManualRefresh(false), 90000)
    try {
      await refreshPdvDut()
    } catch (e) {
      console.error('pdv refresh trigger failed', e)
    }
    poll()
  }, [data, poll])

  useEffect(
    () => () => {
      if (manualTimeoutRef.current) window.clearTimeout(manualTimeoutRef.current)
    },
    [],
  )

  const rows = data?.rows ?? []
  const reached = rows.filter((r) => r.reachable).length
  const showRefreshing = manualRefresh || rows.length === 0

  return (
    <div className="view" data-testid="pdv-root">
      <header className="view-header" data-testid="header">
        <div className="title-block">
          <h1 data-testid="title">Appliance PDV DUT Version</h1>
          <p className="subtitle">
            Software versions on each site&apos;s PDV DUT appliance · IP from the Jenkins job
            default parameter · auto-refresh every 30 min
          </p>
        </div>
        <div className="actions">
          <RefreshButton onRefresh={onRefresh} refreshing={manualRefresh} />
        </div>
      </header>

      <div className="status-row" data-testid="status-row">
        {showRefreshing ? (
          <span className="status-chip updating" data-testid="updating-label">
            <span className="spinner spinner-light" aria-hidden="true" />
            Gathering versions… {rows.length > 0 ? `${reached}/${rows.length} reached` : ''}
          </span>
        ) : (
          <span className="status-chip idle" data-testid="idle-label">
            <span className="dot" aria-hidden="true" />
            Up to date · {reached}/{rows.length} reachable
          </span>
        )}
        <span className="status-chip" data-testid="last-refresh-label">
          Last refresh — {minutesAgo(data?.lastRefresh ?? null, now)}
        </span>
      </div>

      {data?.lastError && rows.length === 0 && (
        <div className="pdv-error-banner" data-testid="pdv-error-banner">
          {data.lastError}
        </div>
      )}

      <main className="view-main">
        <div className="pdv-table-wrap" data-testid="pdv-table-wrap">
          <table className="pdv-table" data-testid="pdv-table">
            <thead>
              <tr>
                <th className="pdv-th-stack">Stack</th>
                <th className="pdv-th-ip">Appliance IP</th>
                {COLS.map((c) => (
                  <th
                    key={c.key}
                    className={`pdv-th-ver${c.key === 'software' ? ' pdv-th-sw' : ''}`}
                  >
                    {c.label}
                  </th>
                ))}
                <th className="pdv-th-host">Hostname</th>
                <th className="pdv-th-status">Status</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <PdvRow key={r.site} row={r} />
              ))}
              {rows.length === 0 && !showRefreshing && (
                <tr>
                  <td colSpan={COLS.length + 4} className="pdv-empty">
                    No data yet — click Refresh.
                  </td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      </main>

      <footer className="view-footer" data-testid="footer">
        Netskope proprietary · RiskInsights · auto-refresh every 30 min · read-only
      </footer>
    </div>
  )
}

function PdvRow({ row }: { row: PdvVersionRow }) {
  const v = row.versions ?? {}
  return (
    <tr className={`pdv-row${row.reachable ? '' : ' pdv-row-fail'}`} data-testid={`pdv-row-${row.site}`}>
      <td className="pdv-stack">{row.displayName}</td>
      <td className="pdv-ip">{row.ip ?? '—'}</td>
      {COLS.map((c) => (
        <td
          key={c.key}
          className={`pdv-ver${c.key === 'software' ? ' pdv-ver-sw' : ''}`}
          data-testid={`pdv-${row.site}-${c.key}`}
        >
          {v[c.key] ?? <span className="pdv-missing">—</span>}
        </td>
      ))}
      <td className="pdv-host">{row.hostname || '—'}</td>
      <td className="pdv-status">
        {row.reachable ? (
          <span className="pdv-ok" title="Reached the DUT and parsed show version-info">
            ✓
          </span>
        ) : (
          <span
            className="pdv-bad"
            title={row.error ?? 'unreachable'}
            data-testid={`pdv-${row.site}-error`}
          >
            ✗
          </span>
        )}
        {row.error && (
          <span className="pdv-err-text" data-testid={`pdv-${row.site}-errtext`}>
            {row.error}
          </span>
        )}
      </td>
    </tr>
  )
}
