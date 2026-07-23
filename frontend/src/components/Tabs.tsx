export type TabKey = 'all' | 'prod' | 'npe'

interface Props {
  tab: TabKey
  onChange: (t: TabKey) => void
}

const TABS: { key: TabKey; label: string }[] = [
  { key: 'all', label: 'All' },
  { key: 'prod', label: 'Prod' },
  { key: 'npe', label: 'NPE' },
]

export function Tabs({ tab, onChange }: Props) {
  return (
    <div className="tabs" data-testid="tabs" role="tablist">
      {TABS.map((t) => {
        const active = tab === t.key
        return (
          <button
            key={t.key}
            role="tab"
            aria-selected={active}
            className={`tab${active ? ' tab-active' : ''}`}
            data-testid={`tab-${t.key}`}
            onClick={() => onChange(t.key)}
          >
            {t.label}
          </button>
        )
      })}
    </div>
  )
}
