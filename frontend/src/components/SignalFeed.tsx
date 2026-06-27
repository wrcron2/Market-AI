import type { JSX } from 'react'
import { TrendingUp, TrendingDown, CheckCircle, XCircle, Zap, Clock } from 'lucide-react'
import type { StagedOrder } from '../types'
import { Card } from './ui/primitives'

interface Props {
  events: FeedEvent[]
}

export interface FeedEvent {
  id: string
  type: 'staged' | 'approved' | 'rejected' | 'executed' | 'failed' | 'debate_failed'
  order?: Partial<StagedOrder>
  message?: string
  timestamp: number
  autoExecuted?: boolean
}

const META: Record<FeedEvent['type'], { icon: JSX.Element; label: string; color: string }> = {
  staged: { icon: <Clock size={14} />, label: 'Staged', color: 'text-signal-yellow' },
  approved: { icon: <CheckCircle size={14} />, label: 'Approved', color: 'text-signal-blue' },
  rejected: { icon: <XCircle size={14} />, label: 'Rejected', color: 'text-signal-red' },
  executed: { icon: <Zap size={14} />, label: 'Executed', color: 'text-signal-green' },
  failed: { icon: <XCircle size={14} />, label: 'Failed', color: 'text-signal-red' },
  debate_failed: { icon: <XCircle size={14} />, label: 'Debate Failed', color: 'text-ink-muted' },
}

/** SignalFeed — real-time, reverse-chronological log of all signal events. */
export function SignalFeed({ events }: Props) {
  return (
    <Card className="overflow-hidden">
      <div className="flex items-center gap-2 border-b border-line-soft px-4 py-3.5">
        <span className="h-2 w-2 animate-pulse-dot rounded-full bg-signal-green" />
        <span className="text-sm font-semibold">Live Signal Feed</span>
      </div>
      {events.length === 0 ? (
        <p className="px-4 py-10 text-center text-sm text-ink-faint">Waiting for signals…</p>
      ) : (
        <div className="mf-scroll max-h-[640px] divide-y divide-line-faint overflow-y-auto">
          {events.map((ev) => (
            <FeedItem key={`${ev.id}-${ev.type}`} event={ev} />
          ))}
        </div>
      )}
    </Card>
  )
}

function FeedItem({ event }: { event: FeedEvent }) {
  const { order } = event
  const isBuy = order?.direction === 'BUY' || order?.direction === 'COVER'
  const m = META[event.type]
  const time = new Date(event.timestamp).toLocaleTimeString()

  return (
    <div className="flex items-center gap-2.5 px-4 py-3">
      <span className="w-[58px] shrink-0 font-mono text-[11px] text-ink-faint">{time}</span>
      <span className={m.color}>{m.icon}</span>
      <span className="text-[13px] text-ink-muted">{m.label}</span>
      {order?.symbol && (
        <>
          <span className="font-mono text-[13px] font-bold">{order.symbol}</span>
          {order.direction &&
            (isBuy ? (
              <TrendingUp size={12} className="text-signal-green" />
            ) : (
              <TrendingDown size={12} className="text-signal-red" />
            ))}
          {order.direction && (
            <span className={`mf-chip ${isBuy ? 'bg-signal-green/15 text-emerald-400' : 'bg-signal-red/15 text-red-400'}`}>
              {order.direction}
            </span>
          )}
          {order.quantity != null && (
            <span className="font-mono text-[12px] text-ink-faint">{order.quantity.toLocaleString()}</span>
          )}
        </>
      )}
      {event.autoExecuted && <span className="mf-chip bg-signal-purple/15 text-violet-300">⚡ AUTO</span>}
      {event.message && <span className="truncate text-[12px] text-ink-faint">{event.message}</span>}
    </div>
  )
}
