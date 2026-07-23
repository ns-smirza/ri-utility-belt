import { useCallback, useEffect, useMemo, useState } from 'react'
import {
  fetchStacks,
  tenantFinderSearch,
  type StackInfo,
  type TenantSearchResult,
} from '../api'

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

export function TenantFinder() {
  const [stacks, setStacks] = useState<StackInfo[]>([])
  const [stack, setStack] = useState('')
  const [query, setQuery] = useState('')
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<TenantSearchResult | null>(null)
  const [copied, setCopied] = useState<number | null>(null)

  useEffect(() => {
    fetchStacks()
      .then((s) => {
        setStacks(s)
        if (s.length && !stack) setStack(s[0].name)
      })
      .catch((e) => console.error('fetchStacks failed', e))
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  const selected = useMemo(() => stacks.find((s) => s.name === stack), [stacks, stack])
  // strip a leading http(s):// and any trailing slashes so pasted URLs work as-is
  const cleanedQuery = useMemo(() => {
    let q = query.trim().replace(/^https?:\/\//i, '').replace(/\/+$/, '')
    return q.trim()
  }, [query])
  const ready = !!stack && cleanedQuery.length >= 2

  const onSearch = useCallback(async () => {
    if (!ready) return
    setBusy(true)
    setResult(null)
    try {
      setResult(await tenantFinderSearch(stack, cleanedQuery))
    } catch (e) {
      setResult({ ok: false, error: String(e) })
    } finally {
      setBusy(false)
    }
  }, [ready, stack, cleanedQuery])

  const copyId = useCallback(async (id: number) => {
    const ok = await copyText(String(id))
    if (ok) {
      setCopied(id)
      window.setTimeout(() => setCopied(null), 1500)
    }
  }, [])

  return (
    <div className="view tenant-finder" data-testid="tenant-finder-root">
      <header className="view-header" data-testid="header">
        <div className="title-block">
          <h1 data-testid="title">Tenant ID Finder</h1>
          <p className="subtitle">
            Look up a tenant ID by org name or domain on a given stack
          </p>
        </div>
      </header>

      <div className="prov-card" data-testid="tf-card">
        <div className="prov-form">
          <label className="prov-field">
            <span className="prov-label">Stack</span>
            <select
              className="prov-select"
              data-testid="tenant-finder-stack-select"
              value={stack}
              onChange={(e) => {
                setStack(e.target.value)
                setResult(null)
              }}
            >
              <option value="" disabled>
                Select a stack…
              </option>
              {stacks.map((s) => (
                <option key={s.name} value={s.name}>
                  {s.displayName} — {s.env.toUpperCase()}
                </option>
              ))}
            </select>
          </label>

          <label className="prov-field prov-search-field">
            <span className="prov-label">Search</span>
            <input
              className="prov-input"
              data-testid="tenant-finder-query-input"
              type="text"
              placeholder="org name or domain, e.g. riskinsights-lon3"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              onKeyDown={(e) => {
                if (e.key === 'Enter') onSearch()
              }}
            />
          </label>

          <div className="prov-actions">
            <button
              className="prov-btn"
              data-testid="tenant-finder-search-button"
              onClick={onSearch}
              disabled={!ready || busy}
            >
              {busy ? 'Searching…' : 'Search'}
            </button>
          </div>
        </div>

        {selected && (
          <p className="prov-context" data-testid="tenant-finder-context">
            Searching <strong>{selected.displayName}</strong> ({selected.env.toUpperCase()})
            {query.trim() && (
              <>
                {' '}for “<strong>{cleanedQuery}</strong>”
              </>
            )}
          </p>
        )}
      </div>

      {(result || busy) && (
        <div className="prov-result-card" data-testid="tenant-finder-result">
          <h2 className="prov-result-title">Results</h2>
          {busy && <p className="prov-loading">Searching…</p>}
          {!busy && result && (
            <>
              {result.ok ? (
                <div data-testid="tenant-finder-result-ok">
                  <p className="tf-count" data-testid="tenant-finder-count">
                    {result.count} match{result.count === 1 ? '' : 'es'}
                    {result.truncated ? ` (showing first ${result.returned})` : ''}
                  </p>
                  {(result.matches ?? []).length === 0 ? (
                    <p className="prov-loading">No matches.</p>
                  ) : (
                    <table className="tf-table" data-testid="tenant-finder-table">
                      <thead>
                        <tr>
                          <th>Tenant ID</th>
                          <th>UI Hostname</th>
                          <th>Name</th>
                          <th>DB</th>
                          <th>Created</th>
                        </tr>
                      </thead>
                      <tbody>
                        {(result.matches ?? []).map((m, i) => (
                          <tr key={i} data-testid={`tenant-finder-row-${i}`}>
                            <td className="tf-id-cell">
                              <code className="tf-id">{m.tenantId}</code>
                              <button
                                className={`tf-copy-icon${copied === m.tenantId ? ' copied' : ''}`}
                                data-testid={`tenant-finder-copy-${i}`}
                                title={copied === m.tenantId ? 'Copied!' : 'Copy tenant ID'}
                                aria-label="Copy tenant ID"
                                onClick={() => m.tenantId != null && copyId(m.tenantId)}
                              >
                                {copied === m.tenantId ? (
                                  <svg
                                    width="15"
                                    height="15"
                                    viewBox="0 0 24 24"
                                    fill="none"
                                    stroke="currentColor"
                                    strokeWidth="2.5"
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                    aria-hidden="true"
                                  >
                                    <polyline points="20 6 9 17 4 12" />
                                  </svg>
                                ) : (
                                  <svg
                                    width="15"
                                    height="15"
                                    viewBox="0 0 24 24"
                                    fill="none"
                                    stroke="currentColor"
                                    strokeWidth="2"
                                    strokeLinecap="round"
                                    strokeLinejoin="round"
                                    aria-hidden="true"
                                  >
                                    <rect x="9" y="9" width="13" height="13" rx="2" ry="2" />
                                    <path d="M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1" />
                                  </svg>
                                )}
                              </button>
                            </td>
                            <td>
                              <code>{m.uiHostname ?? '—'}</code>
                            </td>
                            <td>{m.name ?? '—'}</td>
                            <td>
                              <code>{m.dbname ?? '—'}</code>
                            </td>
                            <td className="tf-created">
                              {m.createTime ? m.createTime.replace(/\.[0-9]+Z$/, 'Z').replace('T', ' ') : '—'}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  )}
                </div>
              ) : (
                <div className="prov-fail" data-testid="tenant-finder-result-fail">
                  <p className="prov-error" data-testid="tenant-finder-error-output">
                    {result.error || 'Search failed'}
                  </p>
                  {result.output && (
                    <pre className="prov-output" data-testid="tenant-finder-output">
                      {result.output}
                    </pre>
                  )}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
  )
}
