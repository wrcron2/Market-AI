import { useState, useEffect, useCallback } from 'react'

export type TradingMode = 'yahoo' | 'ibkr'

interface Props {
  mode: TradingMode
  onChange: (mode: TradingMode) => void
  disabled?: boolean
}

/**
 * TradingModeToggle — Yahoo Finance simulation ↔ IBKR real trading.
 * Yahoo (left): yfinance data + simulated execution, no real money.
 * IBKR (right): live IBKR data + real execution through the Green Light gate.
 */
export function TradingModeToggle({ mode, onChange, disabled = false }: Props) {
  const isIBKR = mode === 'ibkr'
  return (
    <div className="flex gap-2.5">
      <button
        disabled={disabled}
        onClick={() => onChange('yahoo')}
        className={`flex-1 rounded-lg border px-3 py-2.5 text-center text-[13px] font-semibold transition-colors disabled:opacity-50 ${
          !isIBKR ? 'border-signal-blue bg-signal-blue/10 text-blue-200' : 'border-line-soft text-ink-faint hover:border-line'
        }`}
      >
        🧪 Yahoo · Sim
      </button>
      <button
        disabled={disabled}
        onClick={() => onChange('ibkr')}
        className={`flex-1 rounded-lg border px-3 py-2.5 text-center text-[13px] font-semibold transition-colors disabled:opacity-50 ${
          isIBKR ? 'border-signal-red bg-signal-red/10 text-red-300' : 'border-line-soft text-ink-faint hover:border-line'
        }`}
      >
        ⚡ IBKR · Live
      </button>
    </div>
  )
}

/**
 * useTradingMode — manages mode state and syncs with the Go backend.
 * The backend persists the mode so a refresh keeps the brain on the right source.
 */
export function useTradingMode() {
  const [mode, setMode] = useState<TradingMode>('yahoo')
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    fetch('/api/mode')
      .then((r) => r.json())
      .then((data: { mode: TradingMode }) => setMode(data.mode ?? 'yahoo'))
      .catch(() => setMode('yahoo'))
      .finally(() => setLoading(false))
  }, [])

  const changeMode = useCallback(async (next: TradingMode) => {
    setMode(next)
    setError(null)
    try {
      const res = await fetch('/api/mode', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ mode: next }),
      })
      if (!res.ok) throw new Error(await res.text())
      const data: { mode: TradingMode } = await res.json()
      setMode(data.mode)
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to update mode')
      setMode((prev) => (prev === 'ibkr' ? 'yahoo' : 'ibkr'))
    }
  }, [])

  return { mode, changeMode, loading, error }
}
