export type Env = 'prod' | 'npe'

export interface PodInfo {
  name: string
  status: string
}

export interface RolloutInfo {
  /** ISO creationTimestamp of the active ReplicaSet = when the current rollout went live. */
  created: string
}

export interface ImageInfo {
  image: string
  running: boolean
  status: string
  pods: PodInfo[]
  /** Age of the current deployment (active ReplicaSet creation timestamp), if available. */
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
