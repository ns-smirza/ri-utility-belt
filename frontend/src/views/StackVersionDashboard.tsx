import { useCallback, useEffect, useRef, useState } from 'react'
import { toBlob } from 'html-to-image'
import type { DashboardData, Env, Stack } from '../types'
import { fetchData, triggerRefresh } from '../api'
import { Tabs, type TabKey } from '../components/Tabs'
import { RefreshButton } from '../components/RefreshButton'
import { StatusLabels } from '../components/StatusLabels'
import { DataTable } from '../components/DataTable'
import { SearchBar } from '../components/SearchBar'
import { SnapshotButton } from '../components/SnapshotButton'

const POLL_MS = 15000
const AGO_TICK_MS = 30000

function matchesSearch(s: Stack, q: string): boolean {
  if (!q.trim()) return true
  const needle = q.toLowerCase()
  const inName =
    (s.displayName ?? '').toLowerCase().includes(needle) ||
    s.name.toLowerCase().includes(needle)
  if (inName) return true
  if (s.images.some((i) => i.image.toLowerCase().includes(needle))) return true
  return Object.values(s.packages)
    .flat()
    .some((f) => f.toLowerCase().includes(needle))
}

export function StackVersionDashboard() {
  const [data, setData] = useState<DashboardData | null>(null)
  const [tab, setTab] = useState<TabKey>('all')
  const [query, setQuery] = useState('')
  const [fullVersions, setFullVersions] = useState(false)
  const [now, setNow] = useState<number>(() => Date.now())
  // manualRefresh = a refresh the user explicitly triggered (show progress UI).
  // Background/scheduled refreshes swap data silently — no progress UI.
  const [manualRefresh, setManualRefresh] = useState(false)
  const [hasData, setHasData] = useState(false)
  const sawRefreshingRef = useRef(false)
  const prevLastRefreshRef = useRef<string | null>(null)
  const manualTimeoutRef = useRef<number | null>(null)
  const tableRef = useRef<HTMLTableElement | null>(null)
  const scrollRef = useRef<HTMLDivElement | null>(null)

  const poll = useCallback(async () => {
    try {
      const d = await fetchData()
      setData(d)
      if (d.stacks.length > 0) setHasData(true)
      if (d.refreshing) sawRefreshingRef.current = true
      // A manual refresh is done once the backend finishes a refresh cycle
      // (refreshing true -> false) OR once a new dataset arrives (lastRefresh advanced).
      if (manualRefresh) {
        const advanced =
          !!d.lastRefresh && d.lastRefresh !== prevLastRefreshRef.current
        if ((sawRefreshingRef.current && !d.refreshing) || advanced) {
          setManualRefresh(false)
        }
      }
    } catch (e) {
      console.error('poll failed', e)
    }
  }, [manualRefresh])

  useEffect(() => {
    poll()
    // poll faster while a manual refresh is in progress
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
    // safety: never stay stuck in the manual-refresh state
    if (manualTimeoutRef.current) window.clearTimeout(manualTimeoutRef.current)
    manualTimeoutRef.current = window.setTimeout(() => setManualRefresh(false), 120000)
    try {
      await triggerRefresh()
    } catch (e) {
      console.error('refresh trigger failed', e)
    }
    poll()
  }, [data, poll])

  const onSnapshot = useCallback(async () => {
    const table = tableRef.current
    if (!table) throw new Error('table not ready')
    const sc = scrollRef.current
    const fullW = sc?.scrollWidth ?? table.offsetWidth
    const fullH = sc?.scrollHeight ?? table.offsetHeight
    const blob = await toBlob(table, {
      width: fullW,
      height: fullH,
      backgroundColor: '#ffffff',
      pixelRatio: 2,
      style: { width: `${fullW}px` },
    })
    if (!blob) throw new Error('render produced no image')
    if (navigator.clipboard && typeof ClipboardItem !== 'undefined') {
      try {
        await navigator.clipboard.write([new ClipboardItem({ 'image/png': blob })])
        return
      } catch {
        /* fall through to download */
      }
    }
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = 'rca-stacks-snapshot.png'
    document.body.appendChild(a)
    a.click()
    a.remove()
    URL.revokeObjectURL(url)
  }, [])

  const filtered = (data?.stacks ?? [])
    .filter((s) => {
      if (tab !== 'all' && s.env !== (tab as Env)) return false
      return matchesSearch(s, query)
    })
    .sort((a, b) => {
      // On the All tab: NPE first in a fixed order (STG01, QA01, DEVINT), then prod
      // in data order. Stable sort preserves relative order within equal keys.
      const NPE_ORDER: Record<string, number> = { STG01: 0, QA01: 1, DEVINT: 2 }
      const key = (s: Stack) => (s.env === 'npe' ? NPE_ORDER[s.displayName ?? ''] ?? 50 : 1000)
      return key(a) - key(b)
    })

  const totalInTab = (data?.stacks ?? []).filter(
    (s) => tab === 'all' || s.env === (tab as Env),
  ).length

  // Show the "gathering" progress UI only for an explicit user refresh, or
  // before the first dataset has arrived. Background auto-refresh stays silent.
  const showRefreshing = manualRefresh || !hasData

  return (
    <div className="view" data-testid="dashboard-root">
      <header className="view-header" data-testid="header">
        <div className="title-block">
          <div className="title-row">
            <h1
              data-testid="title"
              className="title-toggle"
              title="Click to toggle full version strings"
              role="button"
              tabIndex={0}
              onClick={() => setFullVersions((v) => !v)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  setFullVersions((v) => !v)
                }
              }}
            >
              Stack Version Dashboard
            </h1>
            <span
              className="version-mode-badge"
              data-testid="version-mode-badge"
              aria-label={fullVersions ? 'Showing full version strings' : 'Showing compact versions'}
            >
              {fullVersions ? 'Full' : 'Compact'}
            </span>
          </div>
          <p className="subtitle">Image &amp; package versions across Rancher clusters · click title to toggle</p>
        </div>
        <div className="actions">
          <Tabs tab={tab} onChange={setTab} />
          <SearchBar value={query} onChange={setQuery} />
          <SnapshotButton onSnapshot={onSnapshot} disabled={filtered.length === 0} />
          <RefreshButton onRefresh={onRefresh} refreshing={manualRefresh} />
        </div>
      </header>

      <StatusLabels
        refreshing={showRefreshing}
        lastRefresh={data?.lastRefresh ?? null}
        rancherLastRefresh={data?.rancherLastRefresh ?? null}
        now={now}
      />

      {query.trim() && (
        <div className="results-count" data-testid="results-count">
          Showing {filtered.length} of {totalInTab} stack{totalInTab === 1 ? '' : 's'}
        </div>
      )}

      <main className="view-main">
        <DataTable
          stacks={filtered}
          refreshing={showRefreshing}
          query={query}
          fullVersions={fullVersions}
          tableRef={tableRef}
          scrollRef={scrollRef}
        />
      </main>

      <footer className="view-footer" data-testid="footer">
        Netskope proprietary · RiskInsights · auto-refresh every 5 min
      </footer>
    </div>
  )
}
