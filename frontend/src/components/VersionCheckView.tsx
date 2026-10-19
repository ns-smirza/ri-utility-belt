import { useMemo, useState } from 'react'
import type { Stack } from '../types'
import { imageName, imageTag } from './CompareView'

interface Spec {
  /** Image name without tag (e.g. "alarmmanager"). */
  image: string
  /** Expected tag (e.g. "1.0.39"). */
  version: string
}

interface Props {
  stacks: Stack[]
  refreshing: boolean
}

// Per-image version compliance: the user pins image:version pairs, and each
// gets its own table listing the stacks where that exact tag exists vs those
// where it does not. A stack running the image at another tag lands in
// "Does Not Exist" with its actual tag shown alongside. Session-only state,
// like the Compare view — a page reload starts fresh.
export function VersionCheckView({ stacks, refreshing }: Props) {
  const [specs, setSpecs] = useState<Spec[]>([])
  const [selImage, setSelImage] = useState('')
  const [version, setVersion] = useState('')

  // Union of pod image names across all stacks, sorted — the dropdown source.
  const imageNames = useMemo(() => {
    const set = new Set<string>()
    for (const s of stacks) s.images.forEach((im) => set.add(imageName(im.image)))
    return [...set].sort()
  }, [stacks])

  const canAdd = !!selImage && !!version.trim()

  const addSpec = () => {
    if (!canAdd) return
    const spec = { image: selImage, version: version.trim() }
    if (specs.some((s) => s.image === spec.image && s.version === spec.version)) return
    setSpecs((cur) => [...cur, spec])
    setVersion('')
  }

  const removeSpec = (i: number) => setSpecs((cur) => cur.filter((_, j) => j !== i))

  // Bucket every stack for one spec: exact tag match vs everything else
  // (missing entirely, or present at another tag — actual tag kept for display).
  const bucket = (spec: Spec) => {
    const exists: Stack[] = []
    const missing: { stack: Stack; actual: string | null }[] = []
    for (const s of stacks) {
      const im = s.images.find((i) => imageName(i.image) === spec.image)
      if (!im) missing.push({ stack: s, actual: null })
      else if (imageTag(im.image) === spec.version) exists.push(s)
      else missing.push({ stack: s, actual: imageTag(im.image) })
    }
    return { exists, missing }
  }

  return (
    <div className="vcheck-wrap" data-testid="vcheck-root">
      <div className="table-card vcheck-toolbar">
        {refreshing && (
          <div className="gathering-overlay" data-testid="gathering-overlay">
            <span className="spinner spinner-dark" aria-hidden="true" />
            Gathering data…
          </div>
        )}
        <div className="vcheck-controls">
          <select
            className="prov-select"
            data-testid="vcheck-image-select"
            value={selImage}
            onChange={(e) => setSelImage(e.target.value)}
            aria-label="Pod image"
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
            data-testid="vcheck-version-input"
            type="text"
            placeholder="version, e.g. 1.0.39"
            value={version}
            onChange={(e) => setVersion(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter') addSpec()
            }}
          />
          <button
            className="compare-add vcheck-add-btn"
            data-testid="vcheck-add"
            onClick={addSpec}
            disabled={!canAdd}
          >
            + Add
          </button>
        </div>
      </div>

      {specs.length === 0 ? (
        <div className="table-card compare-empty" data-testid="vcheck-empty">
          <p>Pick an image and a version above to see which stacks have it.</p>
        </div>
      ) : (
        specs.map((spec, i) => {
          const { exists, missing } = bucket(spec)
          const rows = Math.max(exists.length, missing.length)
          return (
            <div
              className="table-card vcheck-card"
              key={`${spec.image}:${spec.version}`}
              data-testid={`vcheck-card-${i}`}
            >
              <div className="vcheck-head">
                <span className="vcheck-title" data-testid={`vcheck-title-${i}`}>
                  {spec.image}
                  <span className="vcheck-ver">:{spec.version}</span>
                </span>
                <span className="vcheck-counts" data-testid={`vcheck-counts-${i}`}>
                  {exists.length} exist · {missing.length} don't
                </span>
                <button
                  className="compare-remove"
                  data-testid={`vcheck-remove-${i}`}
                  onClick={() => removeSpec(i)}
                  title="Remove"
                  aria-label={`Remove ${spec.image}:${spec.version}`}
                >
                  ×
                </button>
              </div>
              <table className="data-table vcheck-table">
                <thead>
                  <tr>
                    <th>Exists</th>
                    <th>Does Not Exist</th>
                  </tr>
                </thead>
                <tbody>
                  {Array.from({ length: rows }, (_, r) => {
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
