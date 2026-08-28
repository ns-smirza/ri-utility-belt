export type Env = 'prod' | 'npe'

export interface PodInfo {
  name: string
  status: string
}

export interface RolloutInfo {
  current: number
  previous?: number | null
}

export interface ImageInfo {
  image: string
  running: boolean
  status: string
  pods: PodInfo[]
  /** Last two deployment revisions (current + previous), if available. */
  rollout?: RolloutInfo | null
}

export interface Stack {
  name: string
  displayName?: string
  env: Env
  images: ImageInfo[]
  packages: Record<string, string[]>
}

export interface DashboardData {
  refreshing: boolean
  lastRefresh: string | null
  rancherLastRefresh: string | null
  stacks: Stack[]
}
