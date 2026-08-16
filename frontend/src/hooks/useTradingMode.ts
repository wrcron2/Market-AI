import { useState, useEffect, useCallback } from 'react'

export type TradingMode = 'yahoo' | 'ibkr'

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
