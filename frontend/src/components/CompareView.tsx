import { useEffect, useMemo, useRef, useState } from 'react'
import type { Stack } from '../types'
import { COLUMNS, handleMultiCellCopy, stripVersion } from './DataTable'

const MAX_STACKS = 5

const PKG_ROWS = COLUMNS.filter((c) => c.kind === 'pkg')

export function imageName(image: string): string {
  const i = image.lastIndexOf(':')
  return i > 0 ? image.slice(0, i) : image
}

export function imageTag(image: string): string {
  const i = image.lastIndexOf(':')
  return i > 0 ? image.slice(i + 1) : image
}

function ageLabel(created?: string | null): string | null {
  if (!created) return null
  const ageMs = Date.now() - Date.parse(created)
  if (Number.isNaN(ageMs) || ageMs < 0) return null
  const days = Math.floor(ageMs / 86_400_000)
  const hours = Math.floor(ageMs / 3_600_000)
  return days >= 1 ? `${days}d` : `${hours}h`
}

interface Props {
  stacks: Stack[]
  refreshing: boolean
  fullVersions: boolean
}

// Transposed comparison table: rows = fields (env, packages, one row per pod
// image), columns = stacks the user picked. Rows where the picked stacks
// disagree get a subtle highlight. Reuses the same /api/data payload — no
// backend involvement.
export function CompareView({ stacks, refreshing, fullVersions }: Props) {
  // Session-only state by design: stack picks and row filters reset on reload.
  const [slots, setSlots] = useState<(string | null)[]>([])
  const [hiddenRows, setHiddenRows] = useState<string[]>([])
  const [rowsOpen, setRowsOpen] = useState(false)
  const rowsRef = useRef<HTMLDivElement | null>(null)

  const byName = useMemo(() => new Map(stacks.map((s) => [s.name, s])), [stacks])

  // Remove keys left behind by the earlier localStorage-persistent version.
  useEffect(() => {
    try {
      localStorage.removeItem('compare-stacks')
      localStorage.removeItem('compare-hidden-rows')
    } catch {
      /* storage unavailable — non-fatal */
    }
  }, [])

  // Close the rows dropdown on outside click / Escape.
  useEffect(() => {
    if (!rowsOpen) return
    const onDoc = (e: MouseEvent) => {
      if (rowsRef.current && !rowsRef.current.contains(e.target as Node)) setRowsOpen(false)
    }
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setRowsOpen(false)
    }
    document.addEventListener('mousedown', onDoc)
    document.addEventListener('keydown', onKey)
    return () => {
      document.removeEventListener('mousedown', onDoc)
      document.removeEventListener('keydown', onKey)
    }
  }, [rowsOpen])

  // Drop saved slots whose stack no longer exists (once data has arrived).
  useEffect(() => {
    if (stacks.length === 0) return
    setSlots((cur) => {
      const next = cur.filter((n) => n === null || byName.has(n))
      return next.length === cur.length ? cur : next
    })
  }, [stacks, byName])

  const picked = slots.map((n) => (n ? byName.get(n) : undefined))
  const pickedStacks = picked.filter((s): s is Stack => !!s)

  // Union of pod image names across the picked stacks, sorted — each gets a row.
  const imageNames = useMemo(() => {
    const set = new Set<string>()
    for (const s of pickedStacks) s.images.forEach((im) => set.add(imageName(im.image)))
    return [...set].sort()
  }, [pickedStacks])

  const addSlot = () => setSlots((cur) => (cur.length < MAX_STACKS ? [...cur, null] : cur))
  const removeSlot = (i: number) => setSlots((cur) => cur.filter((_, j) => j !== i))
  const updateSlot = (i: number, name: string) =>
    setSlots((cur) => cur.map((n, j) => (j === i ? name || null : n)))

  // A row "differs" when the picked stacks don't all have the same value.
  const differs = (values: string[]) => new Set(values).size > 1

  // Row visibility filter: every row header gets a checkbox (all on by
  // default). Row keys: 'env', package keys, and `img:<name>` per pod image.
  const hidden = new Set(hiddenRows)
  const visible = (key: string) => !hidden.has(key)
  const toggleRow = (key: string) =>
    setHiddenRows((cur) => (cur.includes(key) ? cur.filter((k) => k !== key) : [...cur, key]))

  const rowItems: { key: string; label: string; group: boolean }[] = [
    { key: 'env', label: 'Env', group: false },
    ...PKG_ROWS.map((c) => ({ key: c.key, label: c.label, group: false })),
    ...imageNames.map((n) => ({ key: `img:${n}`, label: n, group: true })),
  ]
  const visiblePkgRows = PKG_ROWS.filter((c) => visible(c.key))
  const visibleImageNames = imageNames.filter((n) => visible(`img:${n}`))

  const pkgValue = (s: Stack | undefined, key: string): { display: string[]; cmp: string } => {
    if (!s) return { display: [], cmp: '' }
    const raw = s.packages[key] ?? []
    const display = fullVersions ? raw : raw.map((v) => stripVersion(v, key))
    return { display, cmp: [...display].sort().join('\n') }
  }

  if (slots.length === 0) {
    return (
      <div className="table-card compare-empty" data-testid="compare-empty">
        <p>Pick stacks to compare their versions side by side.</p>
        <button className="compare-add" onClick={addSlot} data-testid="compare-add">
          + Add stack
        </button>
      </div>
    )
  }

  return (
    <div className="compare-wrap" data-testid="compare-root" onCopy={handleMultiCellCopy}>
      {/* Toolbar lives OUTSIDE .table-card: the card has overflow:hidden, which
          would clip the rows dropdown panel at the card's bottom edge. */}
      <div className="compare-toolbar">
        <div className="rows-dropdown" ref={rowsRef}>
          <button
            className="rows-toggle"
            data-testid="compare-rows-toggle"
            aria-expanded={rowsOpen}
            onClick={() => setRowsOpen((o) => !o)}
          >
            Rows ▾
            {hiddenRows.length > 0 && <span className="rows-badge">{hiddenRows.length}</span>}
          </button>
          {rowsOpen && (
            <div className="rows-panel" data-testid="compare-rows-panel" role="menu">
              {(() => {
                const allVisible = rowItems.every((r) => !hidden.has(r.key))
                return (
                  <button
                    className={`rows-all${allVisible ? '' : ' rows-all-select'}`}
                    data-testid="compare-rows-toggle-all"
                    onClick={() =>
                      setHiddenRows(allVisible ? rowItems.map((r) => r.key) : [])
                    }
                  >
                    {allVisible ? 'Deselect All' : 'Select All'}
                  </button>
                )
              })()}
              {rowItems.map((r, idx) => (
                <div key={r.key}>
                  {r.group && (idx === 0 || !rowItems[idx - 1].group) && (
                    <div className="rows-section">Pod Images</div>
                  )}
                  <label className={`rows-item${r.group ? ' rows-item-img' : ''}`}>
                    <input
                      type="checkbox"
                      checked={!hidden.has(r.key)}
                      onChange={() => toggleRow(r.key)}
                      data-testid={`compare-rowcheck-${r.key}`}
                    />
                    <span>{r.label}</span>
                  </label>
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
      <div className="table-card">
        {refreshing && (
          <div className="gathering-overlay" data-testid="gathering-overlay">
            <span className="spinner spinner-dark" aria-hidden="true" />
            Gathering data…
          </div>
        )}
        <div className="table-scroll">
        <table className="data-table compare-table" data-testid="compare-table">
          <thead>
            <tr>
              <th className="compare-field-col">Field</th>
              {slots.map((name, i) => (
                <th key={i} data-testid={`compare-col-${i}`}>
                  <div className="compare-col-head">
                    <select
                      value={name ?? ''}
                      onChange={(e) => updateSlot(i, e.target.value)}
                      data-testid={`compare-select-${i}`}
                      aria-label={`Stack for column ${i + 1}`}
                    >
                      <option value="">Select stack…</option>
                      {stacks.map((s) => (
                        <option
                          key={s.name}
                          value={s.name}
                          disabled={slots.includes(s.name) && slots[i] !== s.name}
                        >
                          {s.displayName || s.name}
                        </option>
                      ))}
                    </select>
                    <button
                      className="compare-remove"
                      onClick={() => removeSlot(i)}
                      title="Remove column"
                      aria-label={`Remove column ${i + 1}`}
                      data-testid={`compare-remove-${i}`}
                    >
                      ×
                    </button>
                  </div>
                </th>
              ))}
              {slots.length < MAX_STACKS && (
                <th className="compare-add-col">
                  <button className="compare-add" onClick={addSlot} data-testid="compare-add">
                    + Add stack
                  </button>
                </th>
              )}
            </tr>
          </thead>
          <tbody>
            {/* Env */}
            {visible('env') && (
              <tr data-testid="compare-row-env">
                <td className="compare-field">Env</td>
                {picked.map((s, i) => {
                  const values = picked.map((p) => p?.env ?? '')
                  const diff = pickedStacks.length > 1 && differs(values)
                  return (
                    <td key={i} className={diff ? 'diff-cell' : ''}>
                      {s ? (
                        <span className={`env-badge env-${s.env}`}>{s.env}</span>
                      ) : (
                        <span className="empty-cell">—</span>
                      )}
                    </td>
                  )
                })}
                {slots.length < MAX_STACKS && <td className="compare-add-col" />}
              </tr>
            )}

            {/* Packages */}
            {visiblePkgRows.map((c) => {
              const vals = picked.map((s) => pkgValue(s, c.key))
              const diff = pickedStacks.length > 1 && differs(vals.map((v) => v.cmp))
              return (
                <tr key={c.key} data-testid={`compare-row-${c.key}`}>
                  <td className="compare-field">{c.label}</td>
                  {vals.map((v, i) => (
                    <td key={i} className={diff ? 'diff-cell' : ''}>
                      {v.display.length === 0 ? (
                        <span className="empty-cell">—</span>
                      ) : (
                        <div className="multi-cell">
                          {v.display.map((d, j) => (
                            <div key={j} className="cell-line">
                              {d}
                            </div>
                          ))}
                        </div>
                      )}
                    </td>
                  ))}
                  {slots.length < MAX_STACKS && <td className="compare-add-col" />}
                </tr>
              )
            })}

            {/* Pod images — one row per image name */}
            {visibleImageNames.length > 0 && (
              <tr className="compare-group" data-testid="compare-group-images">
                <td colSpan={slots.length + (slots.length < MAX_STACKS ? 2 : 1)}>
                  Pod Images
                </td>
              </tr>
            )}
            {visibleImageNames.map((name) => {
              const imgs = picked.map((s) => s?.images.find((im) => imageName(im.image) === name))
              const diff =
                pickedStacks.length > 1 &&
                differs(imgs.map((im) => (im ? imageTag(im.image) : '')))
              return (
                <tr key={name} data-testid={`compare-row-img-${name}`}>
                  <td className="compare-field compare-field-img">{name}</td>
                  {imgs.map((im, i) => {
                    const age = im ? ageLabel(im.rollout?.created) : null
                    return (
                      <td key={i} className={diff ? 'diff-cell' : ''}>
                        {im ? (
                          <span className="pod-line">
                            <span className="pod-dot-wrap" title={im.status}>
                              <span className={`pod-dot ${im.running ? 'pod-on' : 'pod-off'}`} />
                            </span>
                            <span className="compare-img-tag">{imageTag(im.image)}</span>
                            {age && (
                              <span
                                className="rollout-rev rollout-age compare-age"
                                title={`Deployed ${im.rollout?.created ?? ''}`}
                              >
                                {age}
                              </span>
                            )}
                          </span>
                        ) : (
                          <span className="empty-cell">—</span>
                        )}
                      </td>
                    )
                  })}
                  {slots.length < MAX_STACKS && <td className="compare-add-col" />}
                </tr>
              )
            })}
          </tbody>
        </table>
        </div>
      </div>
    </div>
  )
}
