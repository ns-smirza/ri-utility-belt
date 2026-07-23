interface Props {
  onRefresh: () => void
  refreshing: boolean
}

export function RefreshButton({ onRefresh, refreshing }: Props) {
  return (
    <button
      className="refresh-btn"
      data-testid="refresh-button"
      onClick={onRefresh}
      disabled={refreshing}
      title="Refresh the table"
    >
      <span className={`refresh-icon${refreshing ? ' spin' : ''}`} aria-hidden="true">⟳</span>
      {refreshing ? 'Refreshing…' : 'Refresh'}
    </button>
  )
}
