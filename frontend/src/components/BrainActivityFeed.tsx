import { useState, useEffect, useRef } from 'react'
import { Activity } from 'lucide-react'

export interface BrainEvent {
  symbol: string
  step: string      // scan | signal | debate | risk | stage | execute
  status: string    // ok | skip | blocked | error
  detail: string
  timestamp: number
}

const STEP_META: Record<string, { label: string; color: string }> = {
  scan:    { label: 'SCAN',    color: '#60a5fa' },
  signal:  { label: 'SIGNAL',  color: '#a855f7' },
  debate:  { label: 'DEBATE',  color: '#f59e0b' },
  risk:    { label: 'RISK',    color: '#f97316' },
  stage:   { label: 'STAGE',   color: '#3b82f6' },
  execute: { label: 'EXECUTE', color: '#22c55e' },
}

const STATUS_COLORS: Record<string, string> = {
  ok:      '#22c55e',
  skip:    '#94a3b8',
  blocked: '#f59e0b',
  error:   '#ef4444',
}

function timeAgo(ms: number): string {
  const diff = Date.now() - ms
  const m = Math.floor(diff / 60000)
  const h = Math.floor(m / 60)
  if (h > 0) return `${h}h ${m % 60}m ago`
  if (m > 0) return `${m}m ago`
  return 'just now'
}

interface Props {
  /** Live events pushed from the Dashboard's WebSocket connection. */
  liveEvents: BrainEvent[]
}

// Live view of the brain's pipeline: every scan/signal/debate/risk/stage/
// execute step with its outcome and reason. Backfills the last events from
// the backend ring buffer so a page refresh doesn't start blank.
export function BrainActivityFeed({ liveEvents }: Props) {
  const [backfill, setBackfill] = useState<BrainEvent[]>([])
  const seeded = useRef(false)

  useEffect(() => {
    if (seeded.current) return
    seeded.current = true
    fetch('/api/brain/activity')
      .then(r => r.json())
      .then(d => setBackfill(d.events ?? []))
      .catch(() => {})
  }, [])

  // Live events (newest first) followed by backfill, deduped by timestamp+symbol+step.
  const seen = new Set<string>()
  const events: BrainEvent[] = []
  for (const e of [...liveEvents, ...backfill]) {
    const key = `${e.timestamp}-${e.symbol}-${e.step}`
    if (!seen.has(key)) {
      seen.add(key)
      events.push(e)
    }
    if (events.length >= 120) break
  }

  const latest = events[0]

  return (
    <div style={{ background: '#0d1117', border: '1px solid #1e293b', borderRadius: 14, overflow: 'hidden' }}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '12px 18px', borderBottom: '1px solid #1e293b',
      }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Activity size={15} style={{ color: '#a855f7' }} />
          <span style={{ fontSize: 13, fontWeight: 700, color: '#e2e8f0' }}>Brain Activity — Live</span>
          <span style={{ fontSize: 11, color: '#475569' }}>
            scan → signal → debate → risk → stage → execute
          </span>
        </div>
        <span style={{ fontSize: 11, color: '#475569' }}>
          {latest ? `last step ${timeAgo(latest.timestamp)}` : 'no activity yet'}
        </span>
      </div>

      <div style={{ maxHeight: 340, overflowY: 'auto', padding: '8px 0' }}>
        {events.length === 0 && (
          <p style={{ color: '#475569', fontSize: 13, padding: '18px', margin: 0 }}>
            No brain activity received yet. During market hours the brain posts a scan summary
            every bar (~5 min) plus a row for every symbol it skips, blocks, or trades — with the
            reason. If this stays empty while the market is open, the brain container may be down,
            or the backend was just restarted (this feed's history is in-memory and refills on the
            next bar).
          </p>
        )}
        {events.map((e, i) => {
          const step = STEP_META[e.step] ?? { label: e.step.toUpperCase(), color: '#64748b' }
          const statusColor = STATUS_COLORS[e.status] ?? '#64748b'
          return (
            <div key={i} style={{
              display: 'flex', alignItems: 'baseline', gap: 10,
              padding: '5px 18px', fontSize: 12,
              borderBottom: '1px solid #0f172a',
            }}>
              <span style={{ color: '#475569', whiteSpace: 'nowrap', fontSize: 11, minWidth: 62 }}>
                {new Date(e.timestamp).toLocaleTimeString()}
              </span>
              <span style={{ fontWeight: 700, color: '#93c5fd', minWidth: 46 }}>{e.symbol}</span>
              <span style={{
                background: step.color + '18', color: step.color,
                border: `1px solid ${step.color}30`, borderRadius: 4,
                padding: '0px 6px', fontSize: 10, fontWeight: 700, minWidth: 58, textAlign: 'center',
              }}>
                {step.label}
              </span>
              <span style={{ color: statusColor, fontWeight: 700, fontSize: 10, textTransform: 'uppercase', minWidth: 48 }}>
                {e.status}
              </span>
              <span style={{ color: '#94a3b8', flex: 1 }}>{e.detail}</span>
            </div>
          )
        })}
      </div>
    </div>
  )
}
