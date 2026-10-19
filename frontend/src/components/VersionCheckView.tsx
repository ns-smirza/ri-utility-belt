import { useMemo, useState } from 'react'
import type { Stack } from '../types'
import { imageName, imageTag } from './CompareView'

interface Row {
  /** Image name without tag (e.g. "alarmmanager"). */
  image: string
  /** Expected tag (e.g. "1.0.39"); '' = unset. */
  version: string
}

interface Props {
  stacks: Stack[]
  refreshing: boolean
}

// Per-image version compliance: a horizontal checkbox strip lists every pod
// image; checking one appends a control row (static image label + version
// textbox + suggestion pills), unchecking removes it — no "Add image"
// button needed. Every row with a version set becomes a row of ONE table —
// row header is the image:version, columns list the stacks where that exact
// tag exists vs those where it does not (a stack on another tag lands in
// "Does Not Exist" with its actual tag shown). Session-only state, like the
// Compare view — a page reload starts fresh.
export function VersionCheckView({ stacks, refreshing }: Props) {
  const [rows, setRows] = useState<Row[]>([])

  // Union of pod image names across all stacks, sorted — the checkbox source.
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

  const toggleImage = (name: string) =>
    setRows((cur) =>
      cur.some((r) => r.image === name)
        ? cur.filter((r) => r.image !== name)
        : [...cur, { image: name, version: '' }],
    )
  // Select all keeps any versions already typed; unselect all clears everything.
  const selectAll = () =>
    setRows((cur) =>
      imageNames.map(
        (n) => cur.find((r) => r.image === n) ?? { image: n, version: '' },
      ),
    )
  const clearAll = () => setRows([])
  const updateRow = (i: number, patch: Partial<Row>) =>
    setRows((cur) => cur.map((r, j) => (j === i ? { ...r, ...patch } : r)))

  const isComplete = (r: Row) => !!r.version.trim()

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
        <div
          className="vcheck-image-picker"
          data-testid="vcheck-image-picker"
          role="group"
          aria-label="Pod images"
        >
          {imageNames.map((name) => (
            <label
              key={name}
              className={`vcheck-image-check${
                rows.some((r) => r.image === name) ? ' checked' : ''
              }`}
              data-testid={`vcheck-image-check-${name}`}
            >
              <input
                type="checkbox"
                checked={rows.some((r) => r.image === name)}
                onChange={() => toggleImage(name)}
              />
              {name}
            </label>
          ))}
          <span className="vcheck-picker-actions">
            <button
              type="button"
              className="vcheck-picker-btn"
              data-testid="vcheck-select-all"
              disabled={rows.length === imageNames.length}
              onClick={selectAll}
            >
              Select all
            </button>
            <button
              type="button"
              className="vcheck-picker-btn"
              data-testid="vcheck-clear-all"
              disabled={rows.length === 0}
              onClick={clearAll}
            >
              Unselect all
            </button>
          </span>
        </div>
        {rows.length > 0 && (
          <div className="vcheck-rows">
            {rows.map((row, i) => (
              <div className="vcheck-row" key={row.image} data-testid={`vcheck-row-${i}`}>
                <span className="vcheck-row-image" data-testid={`vcheck-image-label-${i}`}>
                  {row.image}
                </span>
                <input
                  className="prov-input"
                  data-testid={`vcheck-version-input-${i}`}
                  type="text"
                  placeholder="version, e.g. 1.0.39"
                  value={row.version}
                  onChange={(e) => updateRow(i, { version: e.target.value })}
                />
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
                <button
                  className="compare-remove"
                  data-testid={`vcheck-remove-${i}`}
                  onClick={() => toggleImage(row.image)}
                  title="Remove"
                  aria-label={`Remove ${row.image}`}
                >
                  ×
                </button>
              </div>
            ))}
          </div>
        )}
      </div>

      {!anyComplete ? (
        <div className="table-card compare-empty" data-testid="vcheck-empty">
          <p>
            {rows.length === 0
              ? 'Tick one or more images above, then pick a version for each.'
              : 'Pick a version above to see which stacks have it.'}
          </p>
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
                  <tr key={row.image} data-testid={`vcheck-trow-${i}`}>
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
