import { useEffect, useMemo, useState } from 'react'
import type { Stack } from '../types'
import { COLUMNS, handleMultiCellCopy, stripVersion } from './DataTable'

const MAX_STACKS = 5
const LS_KEY = 'compare-stacks'

const PKG_ROWS = COLUMNS.filter((c) => c.kind === 'pkg')

function loadSaved(): (string | null)[] {
  try {
    const raw = localStorage.getItem(LS_KEY)
    if (!raw) return []
    const arr: unknown = JSON.parse(raw)
    if (!Array.isArray(arr)) return []
    return arr.filter((x): x is string => typeof x === 'string')
  } catch {
    return []
  }
}

function saveSlots(slots: (string | null)[]) {
  try {
    localStorage.setItem(LS_KEY, JSON.stringify(slots.filter((s) => s !== null)))
  } catch {
    /* storage unavailable (private window etc.) — non-fatal */
  }
}

function imageName(image: string): string {
  const i = image.lastIndexOf(':')
  return i > 0 ? image.slice(0, i) : image
}

function imageTag(image: string): string {
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
  const [slots, setSlots] = useState<(string | null)[]>(loadSaved)

  const byName = useMemo(() => new Map(stacks.map((s) => [s.name, s])), [stacks])

  useEffect(() => saveSlots(slots), [slots])

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
    <div className="table-card" data-testid="compare-root" onCopy={handleMultiCellCopy}>
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

            {/* Packages */}
            {PKG_ROWS.map((c) => {
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
            {imageNames.length > 0 && (
              <tr className="compare-group" data-testid="compare-group-images">
                <td colSpan={slots.length + (slots.length < MAX_STACKS ? 2 : 1)}>
                  Pod Images
                </td>
              </tr>
            )}
            {imageNames.map((name) => {
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
  )
}
