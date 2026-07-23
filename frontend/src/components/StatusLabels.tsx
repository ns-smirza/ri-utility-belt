interface Props {
  refreshing: boolean
  lastRefresh: string | null
  rancherLastRefresh: string | null
  now: number
}

function minutesAgo(iso: string, now: number): string {
  const then = new Date(iso).getTime()
  const diffSec = Math.max(0, Math.floor((now - then) / 1000))
  if (diffSec < 60) return 'just now'
  const m = Math.floor(diffSec / 60)
  if (m < 60) return `${m} min ago`
  const h = Math.floor(m / 60)
  const rem = m % 60
  return `${h}h ${rem}m ago`
}

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

/** Format an epoch ms as a UTC wall-clock "D Month YYYY HH:MM:SS". */
function fmtUtcParts(ms: number): string {
  const d = new Date(ms)
  const p = (n: number) => String(n).padStart(2, '0')
  return (
    `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()} ` +
    `${p(d.getUTCHours())}:${p(d.getUTCMinutes())}:${p(d.getUTCSeconds())}`
  )
}

/** Full timestamp in UTC, TST (Taiwan, UTC+8), PDT (UTC-7), IST (UTC+5:30). */
function rancherTooltip(iso: string): string {
  const epoch = new Date(iso).getTime()
  if (Number.isNaN(epoch)) return 'Rancher files last refreshed — unknown'
  const H = 3600 * 1000
  return [
    `UTC:           ${fmtUtcParts(epoch)}`,
    `TST (Taiwan):  ${fmtUtcParts(epoch + 8 * H)}`,
    `PDT:           ${fmtUtcParts(epoch - 7 * H)}`,
    `IST:           ${fmtUtcParts(epoch + (5 * H + 30 * 60 * 1000))}`,
  ].join('\n')
}

export function StatusLabels({ refreshing, lastRefresh, rancherLastRefresh, now }: Props) {
  return (
    <div className="status-row" data-testid="status-row">
      {refreshing ? (
        <span className="status-chip updating" data-testid="updating-label">
          <span className="spinner spinner-light" aria-hidden="true" />
          Updating… This will take approx 30-60s
        </span>
      ) : (
        <span className="status-chip idle" data-testid="idle-label">
          <span className="dot" aria-hidden="true" />
          Up to date
        </span>
      )}

      <span className="status-chip" data-testid="last-refresh-label">
        Last refresh — {lastRefresh ? minutesAgo(lastRefresh, now) : 'never'}
      </span>

      <span
        className="status-chip tooltip-wrap"
        data-testid="rancher-refresh-label"
      >
        Rancher files last refreshed — {rancherLastRefresh ? minutesAgo(rancherLastRefresh, now) : 'never'}
        {rancherLastRefresh && (
          <span className="tooltip" role="tooltip" data-testid="rancher-refresh-tooltip">
            {rancherTooltip(rancherLastRefresh)}
          </span>
        )}
      </span>
    </div>
  )
}
