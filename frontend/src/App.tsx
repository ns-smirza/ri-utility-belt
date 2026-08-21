import { useEffect, useState } from 'react'
import { Sidebar, type ViewDef } from './components/Sidebar'
import { StackVersionDashboard } from './views/StackVersionDashboard'
import { Provisioning } from './views/Provisioning'
import { TenantFinder } from './views/TenantFinder'
import { VpeTetherDiag } from './views/VpeTetherDiag'
import { PdvDutVersion } from './views/PdvDutVersion'
import './App.css'

// Registry of utility views. To add a new utility: append an entry here
// and add its component to VIEW_COMPONENTS below.
const VIEWS: ViewDef[] = [
  { key: 'stack-versions', label: 'Stack Version Dashboard', icon: '🗂️' },
  { key: 'provisioning', label: 'Provisioning', icon: '⚙️' },
  { key: 'tenant-finder', label: 'Tenant ID Finder', icon: '🔍' },
  { key: 'vpe-tether-diag', label: 'VPE Tethering Diagnosis', icon: '🩺' },
  { key: 'apl-pdv-dut-ver', label: 'Appliance PDV DUT Version', icon: '🖥️' },
]

const VIEW_COMPONENTS: Record<string, () => JSX.Element> = {
  'stack-versions': StackVersionDashboard,
  'provisioning': Provisioning,
  'tenant-finder': TenantFinder,
  'vpe-tether-diag': VpeTetherDiag,
  'apl-pdv-dut-ver': PdvDutVersion,
}

const VIEW_KEYS = new Set(VIEWS.map((v) => v.key))

// The active view is driven by location.hash so each utility has a shareable,
// copyable URL (e.g. http://…:5001/#apl-pdv-dut-ver) and back/forward navigation works.
function readHash(): string {
  const h = window.location.hash.replace(/^#/, '')
  return VIEW_KEYS.has(h) ? h : VIEWS[0].key
}

export default function App() {
  const [view, setView] = useState<string>(readHash)

  useEffect(() => {
    const onHash = () => setView(readHash())
    window.addEventListener('hashchange', onHash)
    return () => window.removeEventListener('hashchange', onHash)
  }, [])

  const handleChange = (key: string) => {
    setView(key)
    if (readHash() !== key) {
      window.location.hash = key
    }
  }

  const Active = VIEW_COMPONENTS[view] ?? StackVersionDashboard

  return (
    <div className="shell" data-testid="shell">
      <Sidebar views={VIEWS} active={view} onChange={handleChange} />
      <main className="content" data-testid="content">
        <Active />
      </main>
    </div>
  )
}
