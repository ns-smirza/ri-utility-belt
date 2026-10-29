import { useEffect, useMemo, useRef, useState } from 'react'
import type { Stack } from '../types'
import { imageName, imageTag } from './CompareView'
import { COLUMNS, stripVersion } from './DataTable'

interface Row {
  /** Image name (kind "image") or package key (kind "pkg"). */
  image: string
  /** Expected tag / compact version (e.g. "1.0.39"); '' = unset. */
  version: string
  kind: 'image' | 'pkg'
}

interface Props {
  stacks: Stack[]
  refreshing: boolean
}

/** Numeric-aware version compare: "3.531" > "3.524", "139.x" > "77.x". */
function cmpVer(a: string, b: string): number {
  const pa = a.split(/[^0-9]+/).filter(Boolean).map(Number)
  const pb = b.split(/[^0-9]+/).filter(Boolean).map(Number)
  for (let i = 0; i < Math.max(pa.length, pb.length); i++) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0)
    if (d) return d
  }
  return a.localeCompare(b)
}

/** Group-level tri-state checkbox — column 1 of the picker table. Checked
    when every item in the group is selected, indeterminate dash when some
    are. Plain box, deliberately not a pill like the item checkboxes. The
    caption is a separate <label htmlFor> in column 2. */
function GroupCheck({
  id,
  label,
  names,
  rows,
  onToggle,
}: {
  id: string
  label: string
  names: string[]
  rows: Row[]
  onToggle: () => void
}) {
  const ref = useRef<HTMLInputElement | null>(null)
  const checkedCount = names.filter((n) => rows.some((r) => r.image === n)).length
  const all = names.length > 0 && checkedCount === names.length
  const some = checkedCount > 0 && !all
  useEffect(() => {
    if (ref.current) ref.current.indeterminate = some
  }, [some])
  return (
    <input
      ref={ref}
      id={id}
      className="vcheck-group-box"
      data-testid={`vcheck-group-${label.toLowerCase()}`}
      type="checkbox"
      checked={all}
      onChange={onToggle}
      aria-label={`Select all ${label}`}
    />
  )
}

// Per-image/package version compliance: a horizontal checkbox strip lists
// every pod image and every package category; checking one appends a control
// row (static label + version textbox + suggestion pills), unchecking removes
// it. Every row with a version set becomes a row of ONE table — row header
// is name:version, columns list the stacks where it exists vs not (a stack
// on another version lands in "Does Not Exist" with its actual/latest version
// shown). Session-only state, like the Compare view — reload starts fresh.
export function VersionCheckView({ stacks, refreshing }: Props) {
  const [rows, setRows] = useState<Row[]>([])

  // Union of pod image names across all stacks — the checkbox source.
  // MP-side services lead the list, the rest follow alphabetically.
  const imageNames = useMemo(() => {
    const FIRST = ['callhome', 'logwatcher', 'nslogcollector']
    const set = new Set<string>()
    for (const s of stacks) s.images.forEach((im) => set.add(imageName(im.image)))
    return [...set].sort((a, b) => {
      const ia = FIRST.indexOf(a)
      const ib = FIRST.indexOf(b)
      if (ia !== -1 || ib !== -1)
        return (ia === -1 ? FIRST.length : ia) - (ib === -1 ? FIRST.length : ib)
      return a.localeCompare(b)
    })
  }, [stacks])

  // Package categories present in the data, in main-table column order.
  const pkgKeys = useMemo(() => {
    const present = new Set<string>()
    for (const s of stacks) Object.keys(s.packages).forEach((k) => present.add(k))
    return COLUMNS.filter((c) => c.kind === 'pkg' && present.has(c.key)).map((c) => c.key)
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

  // Distinct compact versions per package, latest first. Packages hold long
  // histories, so the pill strip scrolls horizontally instead of wrapping
  // (all versions stay reachable without growing the row's height).
  const pkgVersionLists = useMemo(() => {
    const map = new Map<string, Map<string, number>>()
    for (const s of stacks)
      for (const k of pkgKeys)
        for (const raw of s.packages[k] ?? []) {
          const v = stripVersion(raw, k)
          if (!map.has(k)) map.set(k, new Map())
          const m = map.get(k)!
          m.set(v, (m.get(v) ?? 0) + 1)
        }
    const out = new Map<string, { tag: string; count: number }[]>()
    for (const [k, m] of map)
      out.set(
        k,
        [...m.entries()]
          .map(([tag, count]) => ({ tag, count }))
          .sort((a, b) => cmpVer(b.tag, a.tag)),
      )
    return out
  }, [stacks, pkgKeys])

  const toggleRow = (name: string, kind: Row['kind']) =>
    setRows((cur) =>
      cur.some((r) => r.image === name)
        ? cur.filter((r) => r.image !== name)
        : [...cur, { image: name, version: '', kind }],
    )
  // Select all keeps any versions already typed; unselect all clears everything.
  const selectAll = () =>
    setRows((cur) =>
      [
        ...imageNames.map((n) => ({ name: n, kind: 'image' as const })),
        ...pkgKeys.map((k) => ({ name: k, kind: 'pkg' as const })),
      ].map(({ name, kind }) => cur.find((r) => r.image === name) ?? { image: name, version: '', kind }),
    )
  const clearAll = () => setRows([])
  // Group toggle: check = select every item in the group (keeping versions
  // already typed), uncheck = clear just that group. Group order is kept
  // stable: images before packages.
  const toggleGroup = (names: string[], kind: Row['kind']) =>
    setRows((cur) => {
      const allIn = names.every((n) => cur.some((r) => r.image === n))
      if (allIn) return cur.filter((r) => !names.includes(r.image))
      const others = cur.filter((r) => !names.includes(r.image))
      const group = names.map(
        (n) => cur.find((r) => r.image === n) ?? { image: n, version: '', kind },
      )
      return kind === 'image' ? [...group, ...others] : [...others, ...group]
    })
  const updateRow = (i: number, patch: Partial<Row>) =>
    setRows((cur) => cur.map((r, j) => (j === i ? { ...r, ...patch } : r)))

  const isComplete = (r: Row) => !!r.version.trim()

  // Bucket every stack for one row: match vs everything else (missing
  // entirely, or present at another version — actual kept for display).
  // Images have one tag per stack; packages match against the compact
  // version list, and a miss reports only the stack's latest version.
  const bucket = (row: Row) => {
    const version = row.version.trim()
    const exists: Stack[] = []
    const missing: { stack: Stack; actual: string | null }[] = []
    for (const s of stacks) {
      if (row.kind === 'image') {
        const im = s.images.find((i) => imageName(i.image) === row.image)
        if (!im) missing.push({ stack: s, actual: null })
        else if (imageTag(im.image) === version) exists.push(s)
        else missing.push({ stack: s, actual: imageTag(im.image) })
      } else {
        const list = (s.packages[row.image] ?? []).map((v) => stripVersion(v, row.image))
        if (list.includes(version)) exists.push(s)
        else {
          const latest = list.length ? [...list].sort(cmpVer)[list.length - 1] : null
          missing.push({ stack: s, actual: latest })
        }
      }
    }
    return { exists, missing }
  }

  const anyComplete = rows.some(isComplete)
  const totalPickable = imageNames.length + pkgKeys.length

  const renderCheck = (name: string, kind: Row['kind']) => (
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
        onChange={() => toggleRow(name, kind)}
      />
      {name}
    </label>
  )

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
          className="vcheck-picker-groups"
          data-testid="vcheck-image-picker"
          role="group"
          aria-label="Pod images and packages"
        >
          {/* Headerless 3-column table: group tri-state checkbox | build
              type caption | the group's item checkboxes. */}
          <table className="vcheck-picker-table">
            <tbody>
              <tr>
                <td className="vcheck-pt-check">
                  <GroupCheck
                    id="vcheck-group-images-box"
                    label="Images"
                    names={imageNames}
                    rows={rows}
                    onToggle={() => toggleGroup(imageNames, 'image')}
                  />
                </td>
                <td className="vcheck-pt-type">
                  <label htmlFor="vcheck-group-images-box">Images</label>
                </td>
                <td className="vcheck-pt-items">
                  <div className="vcheck-image-picker">
                    {imageNames.map((name) => renderCheck(name, 'image'))}
                  </div>
                </td>
              </tr>
              <tr>
                <td className="vcheck-pt-check">
                  <GroupCheck
                    id="vcheck-group-packages-box"
                    label="Packages"
                    names={pkgKeys}
                    rows={rows}
                    onToggle={() => toggleGroup(pkgKeys, 'pkg')}
                  />
                </td>
                <td className="vcheck-pt-type">
                  <label htmlFor="vcheck-group-packages-box">Packages</label>
                </td>
                <td className="vcheck-pt-items">
                  <div className="vcheck-image-picker">
                    {pkgKeys.map((key) => renderCheck(key, 'pkg'))}
                  </div>
                </td>
              </tr>
            </tbody>
          </table>
          <div className="vcheck-picker-actions">
            <button
              type="button"
              className="vcheck-picker-btn"
              data-testid="vcheck-select-all"
              disabled={rows.length === totalPickable}
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
          </div>
        </div>
        {rows.length > 0 && (
          <div className="vcheck-rows">
            {rows.map((row, i) => {
              const suggestions =
                row.kind === 'image'
                  ? tagsByImage.get(row.image) ?? []
                  : pkgVersionLists.get(row.image) ?? []
              return (
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
                    {suggestions.map(({ tag, count }) => (
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
                    onClick={() => toggleRow(row.image, row.kind)}
                    title="Remove"
                    aria-label={`Remove ${row.image}`}
                  >
                    ×
                  </button>
                </div>
              )
            })}
          </div>
        )}
      </div>

      {!anyComplete ? (
        <div className="table-card compare-empty" data-testid="vcheck-empty">
          <p>
            {rows.length === 0
              ? 'Tick one or more images or packages above, then pick a version for each.'
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
