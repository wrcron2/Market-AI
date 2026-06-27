import { useState, useEffect, useCallback } from 'react'
import { Zap, AlertTriangle } from 'lucide-react'

interface Props {
  enabled: boolean
  onChange: (enabled: boolean) => void
  disabled?: boolean
}

/**
 * AutoExecuteToggle — enables/disables autonomous Alpaca order execution.
 * Enabling requires a typed "ENABLE" confirmation (per the design brief).
 * Disabling is immediate. Defaults to OFF on every backend restart for safety.
 */
export function AutoExecuteToggle({ enabled, onChange, disabled = false }: Props) {
  const [modal, setModal] = useState(false)
  const [text, setText] = useState('')

  const requestToggle = () => {
    if (disabled) return
    if (!enabled) {
      setText('')
      setModal(true)
    } else {
      onChange(false)
    }
  }
  const confirm = () => {
    if (text.trim() === 'ENABLE') {
      onChange(true)
      setModal(false)
    }
  }

  return (
    <>
      <div className="flex items-start justify-between gap-4">
        <div className="flex-1">
          <div className="flex items-center gap-2">
            <Zap size={15} className={enabled ? 'text-signal-yellow' : 'text-ink-faint'} />
            <span className="text-sm font-semibold">AUTO_EXECUTE</span>
            <span className={`mf-chip ${enabled ? 'bg-signal-yellow/18 text-yellow-300' : 'bg-surface-sunken text-ink-faint'}`}>
              {enabled ? 'AUTO ON' : 'AUTO OFF'}
            </span>
          </div>
          <p className="mt-2 text-[12.5px] leading-relaxed text-ink-muted">
            When enabled, the system places orders automatically during market hours{' '}
            <strong className="text-orange-300">without your approval</strong>. This is the only place this can be changed.
          </p>
        </div>
        <button
          role="switch"
          aria-checked={enabled}
          aria-label="Enable or disable autonomous trade execution"
          disabled={disabled}
          onClick={requestToggle}
          className={`relative h-[30px] w-[52px] shrink-0 rounded-full transition-colors disabled:opacity-50 ${
            enabled ? 'bg-signal-yellow' : 'bg-line'
          }`}
        >
          <span
            className={`absolute top-[3px] h-6 w-6 rounded-full bg-white transition-all ${enabled ? 'left-[25px]' : 'left-[3px]'}`}
          />
        </button>
      </div>

      {modal && (
        <div
          onClick={() => setModal(false)}
          className="fixed inset-0 z-[100] flex items-center justify-center bg-black/70 p-5 backdrop-blur-sm"
        >
          <div
            onClick={(e) => e.stopPropagation()}
            className="w-full max-w-[460px] overflow-hidden rounded-2xl border border-line bg-surface-raised shadow-2xl"
          >
            <div className="flex items-center gap-3 border-b border-line-soft px-5 py-4">
              <span className="flex h-9 w-9 items-center justify-center rounded-lg border border-signal-yellow/30 bg-signal-yellow/14 text-signal-yellow">
                <AlertTriangle size={18} />
              </span>
              <div>
                <div className="text-base font-bold">Enable AUTO_EXECUTE</div>
                <div className="text-xs text-ink-muted">Autonomous order placement</div>
              </div>
            </div>
            <div className="px-5 py-5">
              <div className="mb-4 rounded-lg border border-signal-yellow/25 bg-signal-yellow/8 px-3.5 py-3 text-[13px] leading-relaxed text-yellow-300">
                The system will place orders automatically during market hours{' '}
                <strong>without your approval</strong>. Positions will be entered and exited by the AI pipeline.
              </div>
              <label className="mb-1.5 block text-xs text-ink-muted">
                Type <span className="rounded bg-base px-1.5 font-mono font-bold text-ink">ENABLE</span> to confirm
              </label>
              <input
                autoFocus
                value={text}
                onChange={(e) => setText(e.target.value)}
                onKeyDown={(e) => {
                  if (e.key === 'Enter') confirm()
                }}
                placeholder="ENABLE"
                className="w-full rounded-lg border border-line bg-base px-3 py-2.5 font-mono text-[15px] tracking-widest text-ink outline-none focus:border-signal-yellow"
              />
            </div>
            <div className="flex justify-end gap-2.5 border-t border-line-soft px-5 py-3.5">
              <button
                onClick={() => setModal(false)}
                className="rounded-lg border border-line bg-surface-hover px-4 py-2.5 text-[13px] font-semibold text-slate-300 hover:bg-[#222d42]"
              >
                Cancel
              </button>
              <button
                onClick={confirm}
                disabled={text.trim() !== 'ENABLE'}
                className="rounded-lg bg-signal-yellow px-4 py-2.5 text-[13px] font-bold text-base hover:bg-yellow-400 disabled:cursor-not-allowed disabled:bg-[#3b3f29] disabled:text-ink-faint"
              >
                Enable Autonomous Trading
              </button>
            </div>
          </div>
        </div>
      )}
    </>
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
