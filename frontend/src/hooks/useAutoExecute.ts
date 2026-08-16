import { useState, useEffect, useCallback } from 'react'

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
