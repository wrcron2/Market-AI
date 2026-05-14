import { useState, useEffect, useCallback } from 'react'
import { Zap } from 'lucide-react'

interface Props {
  enabled: boolean
  onChange: (enabled: boolean) => void
  disabled?: boolean
}

/**
 * AutoExecuteToggle — enables/disables autonomous Alpaca order execution.
 *
 * OFF (default): Signals stage as PENDING, trader must click Green Light.
 * ON:            Approved signals execute on Alpaca automatically, no click needed.
 *
 * Defaults to OFF on every backend restart for safety.
 */
export function AutoExecuteToggle({ enabled, onChange, disabled = false }: Props) {
  const handleToggle = () => {
    if (disabled) return
    onChange(!enabled)
  }

  return (
    <div className={`auto-exec-wrapper ${disabled ? 'auto-exec-disabled' : ''} ${enabled ? 'auto-exec-active' : ''}`}>
      <Zap size={14} className="auto-exec-icon" />
      <span className={`auto-exec-label ${enabled ? 'auto-exec-label-on' : ''}`}>
        Auto Execute
      </span>

      <button
        role="switch"
        aria-checked={enabled}
        aria-label="Enable or disable autonomous trade execution on Alpaca paper account"
        className={`mode-toggle-track ${enabled ? 'auto-exec-track-on' : 'mode-toggle-off'}`}
        onClick={handleToggle}
        disabled={disabled}
      >
        <span className="mode-toggle-thumb" />
      </button>

      {enabled ? (
        <span className="auto-exec-pill auto-exec-pill-on">⚡ AUTO</span>
      ) : (
        <span className="auto-exec-pill auto-exec-pill-off">⏸ MANUAL</span>
      )}
    </div>
  )
}

/**
 * useAutoExecute — manages auto-execute state and syncs with the Go backend.
 * Defaults to false (safe) if the backend is unreachable.
 */
export function useAutoExecute() {
  const [enabled, setEnabled] = useState(false)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch('/api/auto-execute')
      .then((r) => r.json())
      .then((data: { enabled: boolean }) => setEnabled(data.enabled ?? false))
      .catch(() => setEnabled(false))
      .finally(() => setLoading(false))
  }, [])

  const toggle = useCallback(async (next: boolean) => {
    setEnabled(next)
    try {
      const res = await fetch('/api/auto-execute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: next }),
      })
      if (!res.ok) throw new Error(await res.text())
      const data: { enabled: boolean } = await res.json()
      setEnabled(data.enabled)
    } catch {
      setEnabled((prev) => !prev)
    }
  }, [])

  return { enabled, toggle, loading }
}
