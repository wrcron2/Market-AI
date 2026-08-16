import { useEffect, useState } from 'react'
import { fmtUSD } from '../../lib/format'

interface Props {
  open: boolean
  onClose: () => void
  /** Fires the real halt: AUTO off + POST /api/halt (backend TODO). */
  onConfirm: () => void
  positionsCount: number | null
  grossExposure: number | null
}

/**
 * Kill switch — type-to-confirm dialog (Nocturne pattern). Cancels working
 * orders intent + disables AUTO. Positions are NOT flattened by the backend
 * yet (POST /api/halt is a known backend TODO) — the body copy says so.
 */
export function KillSwitchDialog({ open, onClose, onConfirm, positionsCount, grossExposure }: Props) {
  const [text, setText] = useState('')

  useEffect(() => {
    if (open) setText('')
  }, [open])

  if (!open) return null

  const armed = text.trim() === 'KILL'

  return (
    <div
      onClick={onClose}
      className="fixed inset-0 z-40 flex items-center justify-center bg-black/70 p-5 backdrop-blur-sm"
    >
      <div
        onClick={(e) => e.stopPropagation()}
        className="w-[min(480px,100%)] overflow-hidden rounded-lg bg-surface"
        style={{ boxShadow: '0 0 0 1px #d97b84, 0 16px 40px rgba(0,0,0,.65)' }}
      >
        <div className="border-b border-line-soft px-5 py-4 text-[15px] font-semibold text-signal-red">
          Kill switch
        </div>
        <div className="px-5 py-4 text-[13px] leading-relaxed text-ink-muted">
          Disables AUTO_EXECUTE and posts a halt to the backend (the{' '}
          <span className="font-mono text-[12px]">/api/halt</span> handler is a known backend TODO —
          staged orders are cancelled on the Alpaca side only via the dashboard today). Nothing new
          will fire until you re-arm it.
        </div>
        <div className="mx-5 rounded-lg bg-base px-3 py-3 font-mono text-[12px] leading-[1.6] text-ink-muted">
          {positionsCount != null ? `${positionsCount} open position${positionsCount === 1 ? '' : 's'}` : 'positions unknown'}
          {' · '}
          {grossExposure != null ? `${fmtUSD(grossExposure)} gross` : 'gross unknown'}
          <br />
          manual flatten from Positions if needed
        </div>
        <div className="px-5 pt-4">
          <label className="mb-1.5 block text-xs text-ink-muted">
            Type <span className="rounded bg-base px-1.5 font-mono font-bold text-ink">KILL</span> to confirm
          </label>
          <input
            autoFocus
            value={text}
            onChange={(e) => setText(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && armed) {
                onConfirm()
                onClose()
              }
              if (e.key === 'Escape') onClose()
            }}
            placeholder="KILL"
            className="w-full rounded-lg bg-base px-3 py-2.5 font-mono text-[15px] tracking-widest text-ink outline-none"
            style={{ boxShadow: '0 0 0 1px #3f424d' }}
          />
        </div>
        <div className="flex justify-end gap-2.5 px-5 py-4">
          <button onClick={onClose} className="mf-btn-secondary">
            Cancel
          </button>
          <button
            onClick={() => {
              onConfirm()
              onClose()
            }}
            disabled={!armed}
            className="mf-btn-danger"
          >
            Halt everything
          </button>
        </div>
      </div>
    </div>
  )
}
