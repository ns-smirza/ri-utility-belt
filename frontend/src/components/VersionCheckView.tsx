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

// Per-image version compliance: each row is an independent image+version
// pair ("+ Add image" appends another set of controls). A complete row gets
// its own table listing the stacks where that exact tag exists vs those
// where it does not; a stack on another tag lands in "Does Not Exist" with
// its actual tag shown. Session-only state, like the Compare view — a page
// reload starts fresh.
export function VersionCheckView({ stacks, refreshing }: Props) {
  const [rows, setRows] = useState<Row[]>([{ image: '', version: '' }])

  // Union of pod image names across all stacks, sorted — the dropdown source.
  const imageNames = useMemo(() => {
    const set = new Set<string>()
    for (const s of stacks) s.images.forEach((im) => set.add(imageName(im.image)))
    return [...set].sort()
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
        rows.map((row, i) => {
          if (!isComplete(row)) return null
          const version = row.version.trim()
          const { exists, missing } = bucket(row)
          const rowCount = Math.max(exists.length, missing.length)
          return (
            <div
              className="table-card vcheck-card"
              key={i}
              data-testid={`vcheck-card-${i}`}
            >
              <div className="vcheck-head">
                <span className="vcheck-title" data-testid={`vcheck-title-${i}`}>
                  {row.image}
                  <span className="vcheck-ver">:{version}</span>
                </span>
                <span className="vcheck-counts" data-testid={`vcheck-counts-${i}`}>
                  {exists.length} exist · {missing.length} don't
                </span>
              </div>
              <table className="data-table vcheck-table">
                <thead>
                  <tr>
                    <th>Exists</th>
                    <th>Does Not Exist</th>
                  </tr>
                </thead>
                <tbody>
                  {Array.from({ length: rowCount }, (_, r) => {
                    const have = exists[r]
                    const miss = missing[r]
                    return (
                      <tr key={r}>
                        <td data-testid={have ? `vcheck-exists-${i}-${have.name}` : undefined}>
                          {have && (
                            <span className="vcheck-stack">
                              <span className={`env-badge env-${have.env}`}>{have.env}</span>
                              {have.displayName || have.name}
                            </span>
                          )}
                        </td>
                        <td data-testid={miss ? `vcheck-missing-${i}-${miss.stack.name}` : undefined}>
                          {miss && (
                            <span className="vcheck-stack">
                              <span className={`env-badge env-${miss.stack.env}`}>
                                {miss.stack.env}
                              </span>
                              {miss.stack.displayName || miss.stack.name}
                              {miss.actual !== null && (
                                <span className="vcheck-actual">({miss.actual})</span>
                              )}
                            </span>
                          )}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )
        })
      )}
    </div>
  )
}
