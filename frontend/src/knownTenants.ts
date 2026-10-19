// Known Risk Insights tenants for the Provisioning tenant picker.
// IDs resolved via the Tenant ID Finder on 2026-10-19. Tenant IDs are stable
// per stack, but if a tenant is re-provisioned (new ID) this list must be
// updated. `stack` is the kubeconfig filename — the same value the Stack
// selector uses — so picking a tenant can auto-select its home stack.
export interface KnownTenant {
  id: string
  hostname: string
  stack: string
  stackDisplay: string
}

export const KNOWN_TENANTS: KnownTenant[] = [
  { id: '76316', hostname: 'riskinsights-qa01-vpe-e2e.qa.boomskope.com', stack: 'stork-qa01-mp-npe-iad0-nc1.yaml', stackDisplay: 'QA01' },
  { id: '2568', hostname: 'risk-insights-stg01.stg.boomskope.com', stack: 'stork-stg01-mp-iad0-nc4.yaml', stackDisplay: 'STG01' },
  { id: '109002', hostname: 'riskinsights-stg01-vpe-e2e.stg.boomskope.com', stack: 'stork-stg01-mp-iad0-nc4.yaml', stackDisplay: 'STG01' },
  { id: '2498', hostname: 'risk-insights-qa01.qa.boomskope.com', stack: 'stork-qa01-mp-npe-iad0-nc1.yaml', stackDisplay: 'QA01' },
  { id: '27389', hostname: 'riskinsights-bom3.goskope.com', stack: 'stork-bom3-mp-prod-bom3-nc1.yaml', stackDisplay: 'BOM3' },
  { id: '18316', hostname: 'riskinsights-ruh1-1.goskope.com', stack: 'stork-ruh1-mp-prod-ruh1-nc1.yaml', stackDisplay: 'RUH1' },
  { id: '18319', hostname: 'riskinsights-fra2-1.goskope.com', stack: 'stork-fra2-mp-prod-fra2-nc1.yaml', stackDisplay: 'FRA2' },
  { id: '18324', hostname: 'riskinsights-fr4-1.de.goskope.com', stack: 'c4-fr4.yaml', stackDisplay: 'FR4' },
  { id: '18314', hostname: 'riskinsights-sin2-1.goskope.com', stack: 'stork-sin2-mp-prod-sin2-nc1.yaml', stackDisplay: 'SIN2' },
  { id: '18327', hostname: 'riskinsights-sjc1-1.goskope.com', stack: 'c4-sjc1.yaml', stackDisplay: 'SJC1' },
  { id: '18331', hostname: 'riskinsights-sjc2-1.goskope.com', stack: 'stork-sjc2-mp-prod-sjc2-nc1.yaml', stackDisplay: 'SJC2' },
  { id: '18315', hostname: 'riskinsights-mel2-1.au.goskope.com', stack: 'stork-mel2-mp-mel2-nc1.yaml', stackDisplay: 'MEL2' },
  { id: '18317', hostname: 'riskinsights-am2-1.eu.goskope.com', stack: 'c4-am2.yaml', stackDisplay: 'AM2' },
  { id: '18326', hostname: 'riskinsights-sv5-1.goskope.com', stack: 'c1-sv5.yaml', stackDisplay: 'SV5' },
  { id: '18321', hostname: 'riskinsights-zur2-1.goskope.com', stack: 'stork-zur2-mp-prod-zur2-nc1.yaml', stackDisplay: 'ZUR2' },
  { id: '18323', hostname: 'riskinsights-dfw3-1.goskope.com', stack: 'stork-dfw3-mp-prod-dfw3-nc1.yaml', stackDisplay: 'DFW3' },
]
