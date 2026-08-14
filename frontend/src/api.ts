import type { DashboardData } from './types'

export async function fetchData(): Promise<DashboardData> {
  const res = await fetch('/api/data', { cache: 'no-store' })
  if (!res.ok) throw new Error(`/api/data failed: ${res.status}`)
  return res.json()
}

export async function triggerRefresh(): Promise<void> {
  const res = await fetch('/api/refresh', { method: 'POST' })
  if (!res.ok) throw new Error(`/api/refresh failed: ${res.status}`)
}

export interface RestartResult {
  ok: boolean
  error?: string
  output?: string
  message?: string
  deployment?: string
}

export async function restartDeployment(stack: string, pod: string): Promise<RestartResult> {
  const res = await fetch('/api/restart-deployment', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ stack, pod }),
  })
  return res.json()
}

export interface StackInfo {
  name: string
  displayName: string
  env: 'prod' | 'npe'
}

export async function fetchStacks(): Promise<StackInfo[]> {
  const res = await fetch('/api/stacks', { cache: 'no-store' })
  if (!res.ok) throw new Error(`/api/stacks failed: ${res.status}`)
  const data = await res.json()
  return data.stacks ?? []
}

export interface TenantMatch {
  tenantId: number | null
  name: string | null
  uiHostname: string | null
  dbname: string | null
  description: string | null
  createTime: string | null
}

export interface TenantSearchResult {
  ok: boolean
  error?: string
  output?: string
  query?: string
  count?: number
  returned?: number
  truncated?: boolean
  matches?: TenantMatch[]
}

export async function tenantFinderSearch(
  stack: string,
  query: string,
): Promise<TenantSearchResult> {
  const res = await fetch(
    `/api/tenant-finder/search?stack=${encodeURIComponent(stack)}&query=${encodeURIComponent(
      query,
    )}`,
    { cache: 'no-store' },
  )
  return res.json()
}

export interface ProvStack {
  name: string
  displayName: string
  env: 'prod' | 'npe'
}

export interface ProvFeature {
  key: string
  label: string
  description: string
  flags: string[]
}

export interface ProvFlagState {
  flag: string
  value: string | null
  enabled: boolean | null
  state: string
}

export interface ProvCheckResult {
  ok: boolean
  error?: string
  output?: string
  feature?: string
  flags?: ProvFlagState[]
  allEnabled?: boolean
}

export interface ProvSetResultItem {
  flag: string
  ok: boolean
  error?: string
  output?: string
}

export interface ProvSetResult {
  ok: boolean
  error?: string
  output?: string
  action?: string
  feature?: string
  value?: string
  results?: ProvSetResultItem[]
  summary?: { ok: number; fail: number; total: number }
  verified?: { flags: ProvFlagState[]; allEnabled: boolean } | null
  verifiedAllMatched?: boolean
  verifyError?: string
  verifyOutput?: string
  message?: string
}

export async function fetchProvStacks(): Promise<ProvStack[]> {
  const res = await fetch('/api/provisioner/stacks', { cache: 'no-store' })
  if (!res.ok) throw new Error(`/api/provisioner/stacks failed: ${res.status}`)
  const data = await res.json()
  return data.stacks ?? []
}

export async function fetchProvFeatures(): Promise<ProvFeature[]> {
  const res = await fetch('/api/provisioner/features', { cache: 'no-store' })
  if (!res.ok) throw new Error(`/api/provisioner/features failed: ${res.status}`)
  const data = await res.json()
  return data.features ?? []
}

export async function provCheck(
  stack: string,
  tenant: string,
  flags: string[],
): Promise<ProvCheckResult> {
  const res = await fetch(
    `/api/provisioner/check?stack=${encodeURIComponent(stack)}&tenant=${encodeURIComponent(
      tenant,
    )}&flags=${encodeURIComponent(flags.join(','))}`,
    { cache: 'no-store' },
  )
  return res.json()
}

export async function provSet(
  stack: string,
  tenant: string,
  value: '1' | '0',
  flags: string[],
): Promise<ProvSetResult> {
  const res = await fetch('/api/provisioner/set', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ stack, tenant, value, flags }),
  })
  return res.json()
}

export type DiagMark = 'tick' | 'cross' | 'warn' | 'na'

export interface DiagCheckRow {
  stage: string
  cid: string
  label: string
  mark: DiagMark
  reason: string
  firstFail: boolean
  blocked: boolean
  ignorable: boolean
  what: string
  sources: string[]
  commands: string[]
}

export interface DiagIdentity {
  serial: string
  tenantUrl: string
  tenantId: string
  restToken: string
  identifier: string
}

export interface DiagCounts {
  total: number
  ticks: number
  crosses: number
  warns: number
  na: number
}

export interface DiagConfirmation {
  label: string
  ok: boolean
}

export interface DiagTokenFile {
  present: boolean
  tenantId?: number | string | null
  deviceId?: string | null
  fqdn?: string | null
  licenseKey?: boolean
  expiredAt?: number | null
  expiredAtDate?: string | null
  createdAt?: number | null
  createdAtDate?: string | null
}

export interface DiagRegToken {
  jwtPresent: boolean
  payload: Record<string, unknown>
  did: string
  tid: string | number | null
  fqdn: string
  exp: number | null
  iat: number | null
  expDate: string | null
  iatDate: string | null
  expired: boolean | null
  tokenFile: DiagTokenFile
}

export interface DiagReport {
  ip: string
  hostname: string
  build: string
  captured: string
  ageMin: number | null
  scenario: string
  scenarioName: string
  confidence: string
  reason: string
  status: string
  summaryMessage: string
  likelyCause: string
  firstFailStage: string | null
  identity: DiagIdentity
  counts: DiagCounts
  staleFiltered: number
  cycleAnchor: string | null
  checks: DiagCheckRow[]
  ignorableChecks: DiagCheckRow[]
  confirmation: DiagConfirmation[]
  tetheringStatus: Record<string, unknown>
  reachabilityStatus: Record<string, unknown>
  registrationToken: DiagRegToken
  podsAll: string
  durationSec?: number
}

export interface VpeDiagResult {
  ok: boolean
  report?: DiagReport
  stderr?: string
  error?: string
  output?: string
  returncode?: number
  durationSec?: number
}

export async function runVpeDiag(
  ip: string,
  user?: string,
  password?: string,
): Promise<VpeDiagResult> {
  const res = await fetch('/api/vpe-diag/run', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ip, user, password }),
  })
  return res.json()
}
