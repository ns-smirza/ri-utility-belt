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
