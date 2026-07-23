import { Fragment, type ReactNode, type Ref } from 'react'
import type { ImageInfo, Stack } from '../types'

interface Props {
  stacks: Stack[]
  refreshing: boolean
  query: string
  fullVersions: boolean
  tableRef: Ref<HTMLTableElement>
  scrollRef: Ref<HTMLDivElement>
}

type ColKind = 'stack' | 'env' | 'images' | 'pkg'
interface Column {
  key: string
  label: string
  kind: ColKind
}

// Ordered columns with display labels. Package keys match the categories
// emitted by stacks_build_version.sh (vsp-ais / vsp-said, not vpe-*).
const COLUMNS: Column[] = [
  { key: 'stack', label: 'Stack', kind: 'stack' },
  { key: 'env', label: 'Env', kind: 'env' },
  { key: 'vpe-sf', label: 'Software', kind: 'pkg' },
  { key: 'kvm', label: 'KVM', kind: 'pkg' },
  { key: 'ova', label: 'OVA', kind: 'pkg' },
  { key: 'vsp-swg', label: 'SWG', kind: 'pkg' },
  { key: 'vsp-ais', label: 'AIS', kind: 'pkg' },
  { key: 'vsp-said', label: 'SAID', kind: 'pkg' },
  { key: 'vpe-content', label: 'Content', kind: 'pkg' },
  { key: 'vpe-geoipdb', label: 'GeoIP DB', kind: 'pkg' },
  { key: 'images', label: 'Pod Images', kind: 'images' },
]

// Per-column prefix/suffix to strip in the compact (default) view.
const STRIP: Record<string, { prefix?: string; suffix?: string }> = {
  'vpe-sf': { prefix: 'vpe-upgrade-sf-', suffix: '.pkg' },
  kvm: { prefix: 'NSKVM-', suffix: '.zip' },
  ova: { prefix: 'NSOVA-', suffix: '.zip' },
  'vsp-swg': { prefix: 'vsp-swg-', suffix: '.pkg' },
  'vsp-ais': { prefix: 'vsp-ais-', suffix: '.pkg' },
  'vsp-said': { prefix: 'vsp-said-', suffix: '.pkg' },
  'vpe-content': { prefix: 'vpe-upgrade-content-', suffix: '.pkg' },
  'vpe-geoipdb': { prefix: 'vpe-upgrade-geoipdb-', suffix: '.pkg' },
}

function stripVersion(value: string, key: string): string {
  const s = STRIP[key]
  if (!s) return value
  let out = value
  if (s.prefix && out.startsWith(s.prefix)) out = out.slice(s.prefix.length)
  if (s.suffix && out.endsWith(s.suffix)) out = out.slice(0, out.length - s.suffix.length)
  // drop a leading develop-branch tag like "1.develop-" (e.g. qa01 builds)
  out = out.replace(/^\d+\.develop-/, '')
  return out
}

function sanitize(name: string): string {
  return name.replace(/[^a-zA-Z0-9]/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '')
}

/** Wrap every case-insensitive occurrence of `query` in <mark>. */
function highlight(text: string, query: string): ReactNode {
  const q = query.trim()
  if (!q) return text
  const needle = q.toLowerCase()
  const lower = text.toLowerCase()
  const out: ReactNode[] = []
  let i = 0
  let k = 0
  let idx = lower.indexOf(needle, i)
  while (idx !== -1) {
    if (idx > i) out.push(<Fragment key={k++}>{text.slice(i, idx)}</Fragment>)
    out.push(
      <mark key={k++} className="hl" data-testid="hl">
        {text.slice(idx, idx + needle.length)}
      </mark>,
    )
    i = idx + needle.length
    idx = lower.indexOf(needle, i)
  }
  if (i < text.length) out.push(<Fragment key={k++}>{text.slice(i)}</Fragment>)
  return out
}

function Cell({
  values,
  query,
  titles,
}: {
  values: string[]
  query: string
  titles?: string[]
}) {
  if (!values || values.length === 0) {
    return <span className="empty-cell">—</span>
  }
  return (
    <div className="multi-cell">
      {values.map((v, i) => {
        const tip = titles && titles[i] !== v ? titles[i] : undefined
        return (
          <div key={i} className="cell-line" title={tip}>
            {highlight(v, query)}
          </div>
        )
      })}
    </div>
  )
}

function ImagesCell({ images, query }: { images: ImageInfo[]; query: string }) {
  if (!images || images.length === 0) {
    return <span className="empty-cell">—</span>
  }
  return (
    <div className="multi-cell">
      {images.map((im, i) => {
        const tip = im.pods.map((p) => `${p.name}: ${p.status}`).join('\n')
        return (
          <div key={i} className="cell-line pod-line">
            <span className="pod-dot-wrap" data-testid={`pod-dot-${i}`}>
              <span className={`pod-dot ${im.running ? 'pod-on' : 'pod-off'}`} />
              <span className="pod-tip" role="tooltip">
                {tip}
              </span>
            </span>
            {highlight(im.image, query)}
          </div>
        )
      })}
    </div>
  )
}

export function DataTable({ stacks, refreshing, query, fullVersions, tableRef, scrollRef }: Props) {
  return (
    <div className="table-card" data-testid="table-card">
      {refreshing && (
        <div className="gathering-overlay" data-testid="gathering-overlay">
          <span className="spinner spinner-dark" aria-hidden="true" />
          Gathering data…
        </div>
      )}
      <div className="table-scroll" ref={scrollRef}>
        <table className="data-table" data-testid="data-table" ref={tableRef}>
          <thead data-testid="thead">
            <tr>
              {COLUMNS.map((c) => (
                <th key={c.key} data-testid={`th-${c.key}`}>
                  {c.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {stacks.length === 0 && (
              <tr data-testid="empty-row">
                <td colSpan={COLUMNS.length} className="empty-state">
                  No stacks to display
                </td>
              </tr>
            )}
            {stacks.map((s) => {
              const sid = sanitize(s.name)
              return (
                <tr key={s.name} data-testid={`row-${sid}`}>
                  {COLUMNS.map((c) => {
                    const testId = `cell-${sid}-${c.key}`
                    if (c.kind === 'stack') {
                      return (
                        <td key={c.key} className="stack-cell" data-testid={testId}>
                          <span
                            className="stack-display"
                            data-testid={`stack-name-${sid}`}
                            title={s.name}
                          >
                            {highlight(s.displayName || s.name, query)}
                          </span>
                        </td>
                      )
                    }
                    if (c.kind === 'env') {
                      return (
                        <td key={c.key} data-testid={testId}>
                          <span
                            className={`env-badge env-${s.env}`}
                            data-testid={`env-badge-${sid}`}
                          >
                            {s.env}
                          </span>
                        </td>
                      )
                    }
                    if (c.kind === 'images') {
                      return (
                        <td key={c.key} data-testid={testId}>
                          <ImagesCell images={s.images} query={query} />
                        </td>
                      )
                    }
                    const raw = s.packages[c.key] ?? []
                    const display = fullVersions ? raw : raw.map((v) => stripVersion(v, c.key))
                    return (
                      <td key={c.key} data-testid={testId}>
                        <Cell
                          values={display}
                          query={query}
                          titles={fullVersions ? undefined : raw}
                        />
                      </td>
                    )
                  })}
                </tr>
              )
            })}
          </tbody>
        </table>
      </div>
    </div>
  )
}
