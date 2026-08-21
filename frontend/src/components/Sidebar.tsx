export interface ViewDef {
  key: string
  label: string
  icon: string
}

interface Props {
  views: ViewDef[]
  active: string
  onChange: (key: string) => void
}

export function Sidebar({ views, active, onChange }: Props) {
  return (
    <aside className="sidebar" data-testid="sidebar">
      <div className="sidebar-brand" data-testid="app-title">
        <span className="brand-icon" aria-hidden="true">🛠️</span>
        <div className="brand-text">
          <div className="brand-title">Virtual Private Edge</div>
          <div className="brand-subtitle">VPE - Utility Belt</div>
        </div>
      </div>

      <nav className="sidebar-nav" data-testid="sidebar-nav" aria-label="Utilities">
        {views.map((v) => {
          const isActive = active === v.key
          return (
            <a
              key={v.key}
              href={`#${v.key}`}
              className={`nav-item${isActive ? ' nav-active' : ''}`}
              data-testid={`nav-${v.key}`}
              aria-current={isActive ? 'page' : undefined}
              onClick={(e) => {
                // Let the href be copyable/shareable, but drive navigation through
                // onChange so state + hash stay in sync (and back/forward work).
                e.preventDefault()
                onChange(v.key)
              }}
            >
              <span className="nav-icon" aria-hidden="true">
                {v.icon}
              </span>
              <span className="nav-label">{v.label}</span>
            </a>
          )
        })}
      </nav>

      <div className="sidebar-footer" data-testid="sidebar-footer">
        Netskope · RiskInsights
      </div>
    </aside>
  )
}
