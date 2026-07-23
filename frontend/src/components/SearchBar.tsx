interface Props {
  value: string
  onChange: (v: string) => void
}

export function SearchBar({ value, onChange }: Props) {
  return (
    <div className="search-bar" data-testid="search-bar">
      <span className="search-icon" aria-hidden="true">⌕</span>
      <input
        type="text"
        className="search-input"
        data-testid="search-input"
        placeholder="Search stack name or version…"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        aria-label="Search stacks"
      />
      {value && (
        <button
          type="button"
          className="search-clear"
          data-testid="search-clear"
          onClick={() => onChange('')}
          aria-label="Clear search"
        >
          ✕
        </button>
      )}
    </div>
  )
}
