import { useState, useEffect, useCallback } from 'react'

export type TradingMode = 'yahoo' | 'ibkr'

interface Props {
  mode: TradingMode
  onChange: (mode: TradingMode) => void
  disabled?: boolean
}

/**
 * TradingModeToggle — Yahoo Finance simulation ↔ IBKR real trading switch.
 *
 * Layout:   Yahoo  [●──────]  IBKR
 *           (OFF)            (ON)
 *
 * - Yahoo mode (OFF/left): uses yfinance data + simulated order execution.
 *   No real money at risk. No API key required — yfinance is free.
 * - IBKR mode (ON/right):  uses live IBKR TWS data + real order execution
 *   through the Green Light gate.
 */
export function TradingModeToggle({ mode, onChange, disabled = false }: Props) {
  const isIBKR = mode === 'ibkr'

  const handleToggle = () => {
    if (disabled) return
    onChange(isIBKR ? 'yahoo' : 'ibkr')
  }

  return (
    <div className={`mode-toggle-wrapper ${disabled ? 'mode-toggle-disabled' : ''}`}>
      {/* Yahoo label */}
      <span className={`mode-label mode-label-yahoo ${!isIBKR ? 'mode-label-active' : ''}`}>
        Yahoo
      </span>

      {/* Toggle track + thumb */}
      <button
        role="switch"
        aria-checked={isIBKR}
        aria-label="Switch between Yahoo Finance simulation and IBKR live trading"
        className={`mode-toggle-track ${isIBKR ? 'mode-toggle-on' : 'mode-toggle-off'}`}
        onClick={handleToggle}
        disabled={disabled}
      >
        <span className="mode-toggle-thumb" />
      </button>

      {/* IBKR label */}
      <span className={`mode-label mode-label-ibkr ${isIBKR ? 'mode-label-active' : ''}`}>
        IBKR
      </span>

      {/* Mode pill badge */}
      {isIBKR ? (
        <span className="mode-pill mode-pill-live">⚡ LIVE</span>
      ) : (
        <span className="mode-pill mode-pill-sim">🧪 SIM</span>
      )}
    </div>
  )
}

/**
 * useTradingMode — manages mode state and syncs with the Go backend.
 *
 * The backend persists the mode so that if the page refreshes, the Python
 * brain is already using the correct data source and executor.
 */
export function useTradingMode() {
  const [mode, setMode] = useState<TradingMode>('yahoo')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  // Fetch current mode from backend on mount
  useEffect(() => {
    fetch('/api/mode')
      .then((r) => r.json())
      .then((data: { mode: TradingMode }) => {
        setMode(data.mode ?? 'yahoo')
      })
      .catch(() => {
        // Backend not yet up — default to yahoo (safe)
        setMode('yahoo')
      })
      .finally(() => setLoading(false))
  }, [])

  // Push mode change to backend
  const changeMode = useCallback(async (next: TradingMode) => {
    // Optimistic update
    setMode(next)
    setError(null)

    try {
      const res = await fetch('/api/mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: next }),
      })
      if (!res.ok) {
        const text = await res.text()
        throw new Error(text)
      }
      const data: { mode: TradingMode } = await res.json()
      setMode(data.mode)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update mode')
      // Revert on failure
      setMode((prev) => (prev === 'ibkr' ? 'yahoo' : 'ibkr'))
    }
  }, [])

  return { mode, changeMode, loading, error }
}
