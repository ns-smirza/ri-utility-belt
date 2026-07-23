import { useState } from 'react'

interface Props {
  onSnapshot: () => Promise<void>
  disabled: boolean
}

export function SnapshotButton({ onSnapshot, disabled }: Props) {
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')

  const handleClick = async () => {
    setBusy(true)
    setMsg('Capturing…')
    try {
      await onSnapshot()
      setMsg('Copied to clipboard ✓')
    } catch (e) {
      console.error('snapshot failed', e)
      setMsg('Snapshot failed')
    } finally {
      setBusy(false)
      window.setTimeout(() => setMsg(''), 2800)
    }
  }

  return (
    <span className="snapshot-wrap">
      <button
        className="snapshot-btn"
        data-testid="snapshot-button"
        onClick={handleClick}
        disabled={disabled || busy}
        title="Copy the full table to clipboard as an image"
      >
        <span aria-hidden="true">📸</span>
        {busy ? 'Capturing…' : 'Snapshot'}
      </button>
      {msg && (
        <span className="snapshot-feedback" data-testid="snapshot-feedback">
          {msg}
        </span>
      )}
    </span>
  )
}
