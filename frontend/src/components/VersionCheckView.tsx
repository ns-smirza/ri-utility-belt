import { useMemo, useState } from 'react'
import type { Stack } from '../types'
import { imageName, imageTag } from './CompareView'

interface Row {
  /** Image name without tag (e.g. "alarmmanager"); '' = unset. */
  image: string
  /** Expected tag (e.g. "1.0.39"); '' = unset. */
  version: string
}

interface Props {
  stacks: Stack[]
  refreshing: boolean
}

// Per-image version compliance: each control row is an independent
// image+version pair ("+ Add image" appends another set of controls).
// Complete rows render as rows of ONE table — row header is the
// image:version, columns list the stacks where that exact tag exists vs
// those where it does not (a stack on another tag lands in "Does Not
// Exist" with its actual tag shown). Session-only state, like the Compare
// view — a page reload starts fresh.
export function VersionCheckView({ stacks, refreshing }: Props) {
  const [rows, setRows] = useState<Row[]>([{ image: '', version: '' }])

  // Union of pod image names across all stacks, sorted — the dropdown source.
  const imageNames = useMemo(() => {
    const set = new Set<string>()
    for (const s of stacks) s.images.forEach((im) => set.add(imageName(im.image)))
    return [...set].sort()
  }, [stacks])

  // Distinct tags per image with stack counts, sorted most-common first —
  // the version suggestion pills. Typing a custom version stays possible.
  const tagsByImage = useMemo(() => {
    const map = new Map<string, Map<string, number>>()
    for (const s of stacks)
      for (const im of s.images) {
        const name = imageName(im.image)
        const tag = imageTag(im.image)
        if (!map.has(name)) map.set(name, new Map())
        const tags = map.get(name)!
        tags.set(tag, (tags.get(tag) ?? 0) + 1)
      }
    const out = new Map<string, { tag: string; count: number }[]>()
    for (const [name, tags] of map)
      out.set(
        name,
        [...tags.entries()]
          .map(([tag, count]) => ({ tag, count }))
          .sort((a, b) => b.count - a.count || a.tag.localeCompare(b.tag)),
      )
    return out
  }, [stacks])

  const updateRow = (i: number, patch: Partial<Row>) =>
    setRows((cur) => cur.map((r, j) => (j === i ? { ...r, ...patch } : r)))
  const removeRow = (i: number) => setRows((cur) => cur.filter((_, j) => j !== i))
  const addRow = () => setRows((cur) => [...cur, { image: '', version: '' }])

  const isComplete = (r: Row) => !!r.image && !!r.version.trim()

  // Bucket every stack for one row: exact tag match vs everything else
  // (missing entirely, or present at another tag — actual tag kept for display).
  const bucket = (row: Row) => {
    const version = row.version.trim()
    const exists: Stack[] = []
    const missing: { stack: Stack; actual: string | null }[] = []
    for (const s of stacks) {
      const im = s.images.find((i) => imageName(i.image) === row.image)
      if (!im) missing.push({ stack: s, actual: null })
      else if (imageTag(im.image) === version) exists.push(s)
      else missing.push({ stack: s, actual: imageTag(im.image) })
    }
    return { exists, missing }
  }

  const anyComplete = rows.some(isComplete)

  return (
    <div className="vcheck-wrap" data-testid="vcheck-root">
      <div className="table-card vcheck-toolbar">
        {refreshing && (
          <div className="gathering-overlay" data-testid="gathering-overlay">
            <span className="spinner spinner-dark" aria-hidden="true" />
            Gathering data…
          </div>
        )}
        <div className="vcheck-rows">
          {rows.map((row, i) => (
            <div className="vcheck-row" key={i} data-testid={`vcheck-row-${i}`}>
              <select
                className="prov-select"
                data-testid={`vcheck-image-select-${i}`}
                value={row.image}
                onChange={(e) => updateRow(i, { image: e.target.value })}
                aria-label={`Pod image for row ${i + 1}`}
              >
                <option value="">Select image…</option>
                {imageNames.map((n) => (
                  <option key={n} value={n}>
                    {n}
                  </option>
                ))}
              </select>
              <input
                className="prov-input"
                data-testid={`vcheck-version-input-${i}`}
                type="text"
                placeholder="version, e.g. 1.0.39"
                value={row.version}
                onChange={(e) => updateRow(i, { version: e.target.value })}
              />
              {row.image && (
                <div
                  className="vcheck-pills"
                  data-testid={`vcheck-pills-${i}`}
                  role="group"
                  aria-label={`Version suggestions for ${row.image}`}
                >
                  {(tagsByImage.get(row.image) ?? []).map(({ tag, count }) => (
                    <button
                      key={tag}
                      type="button"
                      className={`vcheck-pill${
                        row.version.trim() === tag ? ' active' : ''
                      }`}
                      data-testid={`vcheck-pill-${i}-${tag}`}
                      title={`${count} stack${count === 1 ? '' : 's'} on ${tag}`}
                      onClick={() => updateRow(i, { version: tag })}
                    >
                      {tag}
                    </button>
                  ))}
                </div>
              )}
              <button
                className="compare-remove"
                data-testid={`vcheck-remove-${i}`}
                onClick={() => removeRow(i)}
                title="Remove"
                aria-label={`Remove row ${i + 1}`}
              >
                ×
              </button>
            </div>
          ))}
          <button
            className="compare-add vcheck-add-btn"
            data-testid="vcheck-add"
            onClick={addRow}
          >
            + Add image
          </button>
        </div>
      </div>

      {!anyComplete ? (
        <div className="table-card compare-empty" data-testid="vcheck-empty">
          <p>Pick an image and a version above to see which stacks have it.</p>
        </div>
      ) : (
        <div className="table-card vcheck-card" data-testid="vcheck-card">
          <table className="vcheck-table" data-testid="vcheck-table">
            <thead>
              <tr>
                <th className="vcheck-th-image">Image</th>
                <th className="vcheck-th">Exists</th>
                <th className="vcheck-th">Does Not Exist</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => {
                if (!isComplete(row)) return null
                const version = row.version.trim()
                const { exists, missing } = bucket(row)
                return (
                  <tr key={i} data-testid={`vcheck-trow-${i}`}>
                    <th className="vcheck-title" data-testid={`vcheck-title-${i}`}>
                      {row.image}
                      <span className="vcheck-ver">:{version}</span>
                    </th>
                    {/* Chips instead of one row per stack — a table of 19
                        rows took too much vertical space. Chips are
                        env-tinted (prod/npe soft colors), so no separate
                        env badge is needed. */}
                    <td data-testid={`vcheck-exists-col-${i}`}>
                      <div className="vcheck-chips">
                        <span className="vcheck-count vcheck-col-yes">
                          {exists.length}
                        </span>
                        {exists.length === 0 && (
                          <span className="vcheck-none">none</span>
                        )}
                        {exists.map((s) => (
                          <span
                            key={s.name}
                            className={`vcheck-chip env-${s.env}`}
                            data-testid={`vcheck-exists-${i}-${s.name}`}
                          >
                            {s.displayName || s.name}
                          </span>
                        ))}
                      </div>
                    </td>
                    <td data-testid={`vcheck-missing-col-${i}`}>
                      <div className="vcheck-chips">
                        <span className="vcheck-count vcheck-col-no">
                          {missing.length}
                        </span>
                        {missing.length === 0 && (
                          <span className="vcheck-none">none</span>
                        )}
                        {missing.map((m) => (
                          <span
                            key={m.stack.name}
                            className={`vcheck-chip env-${m.stack.env}`}
                            data-testid={`vcheck-missing-${i}-${m.stack.name}`}
                          >
                            {m.stack.displayName || m.stack.name}
                            {m.actual !== null && (
                              <span className="vcheck-actual">{m.actual}</span>
                            )}
                          </span>
                        ))}
                      </div>
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
