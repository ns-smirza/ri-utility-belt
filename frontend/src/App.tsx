import { useState } from 'react'
import { Sidebar, type ViewDef } from './components/Sidebar'
import { StackVersionDashboard } from './views/StackVersionDashboard'
import { Provisioning } from './views/Provisioning'
import { TenantFinder } from './views/TenantFinder'
import { VpeTetherDiag } from './views/VpeTetherDiag'
import './App.css'

// Registry of utility views. To add a new utility: append an entry here
// and add its component to VIEW_COMPONENTS below.
const VIEWS: ViewDef[] = [
  { key: 'stack-versions', label: 'Stack Version Dashboard', icon: '🗂️' },
  { key: 'provisioning', label: 'Provisioning', icon: '⚙️' },
  { key: 'tenant-finder', label: 'Tenant ID Finder', icon: '🔍' },
  { key: 'vpe-tether-diag', label: 'VPE Tethering Diagnosis', icon: '🩺' },
]

const VIEW_COMPONENTS: Record<string, () => JSX.Element> = {
  'stack-versions': StackVersionDashboard,
  'provisioning': Provisioning,
  'tenant-finder': TenantFinder,
  'vpe-tether-diag': VpeTetherDiag,
}

export default function App() {
  const [view, setView] = useState<string>(VIEWS[0].key)
  const Active = VIEW_COMPONENTS[view] ?? StackVersionDashboard

  return (
    <div className="shell" data-testid="shell">
      <Sidebar views={VIEWS} active={view} onChange={setView} />
      <main className="content" data-testid="content">
        <Active />
      </main>
    </div>
  )
}
