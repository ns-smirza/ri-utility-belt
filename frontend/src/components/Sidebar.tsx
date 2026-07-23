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
          <div className="brand-title">Risk Insights</div>
          <div className="brand-subtitle">Utility Belt</div>
        </div>
      </div>

      <nav className="sidebar-nav" data-testid="sidebar-nav" aria-label="Utilities">
        {views.map((v) => {
          const isActive = active === v.key
          return (
            <button
              key={v.key}
              type="button"
              className={`nav-item${isActive ? ' nav-active' : ''}`}
              data-testid={`nav-${v.key}`}
              aria-current={isActive ? 'page' : undefined}
              onClick={() => onChange(v.key)}
            >
              <span className="nav-icon" aria-hidden="true">
                {v.icon}
              </span>
              <span className="nav-label">{v.label}</span>
            </button>
          )
        })}
      </nav>

      <div className="sidebar-footer" data-testid="sidebar-footer">
        Netskope · RiskInsights
      </div>
    </aside>
  )
}
