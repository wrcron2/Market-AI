import type { JSX } from 'react'
import { TrendingUp, TrendingDown, CheckCircle, XCircle, Zap, Clock } from 'lucide-react'
import type { StagedOrder } from '../types'

interface Props {
  events: FeedEvent[]
}

export interface FeedEvent {
  id: string
  type: 'staged' | 'approved' | 'rejected' | 'executed' | 'failed'
  order?: Partial<StagedOrder>
  message?: string
  timestamp: number
}

const statusIcon: Record<FeedEvent['type'], JSX.Element> = {
  staged:   <Clock size={14} className="feed-icon staged" />,
  approved: <CheckCircle size={14} className="feed-icon approved" />,
  rejected: <XCircle size={14} className="feed-icon rejected" />,
  executed: <Zap size={14} className="feed-icon executed" />,
  failed:   <XCircle size={14} className="feed-icon failed" />,
}

const statusLabel: Record<FeedEvent['type'], string> = {
  staged:   'Staged',
  approved: 'Approved',
  rejected: 'Rejected',
  executed: 'Executed',
  failed:   'Failed',
}

/**
 * SignalFeed — a real-time, reverse-chronological log of all signal events
 * broadcast over the WebSocket connection.
 */
export function SignalFeed({ events }: Props) {
  return (
    <div className="signal-feed">
      <h2 className="panel-title">Live Signal Feed</h2>
      {events.length === 0 ? (
        <p className="feed-empty">Waiting for signals…</p>
      ) : (
        <ul className="feed-list">
          {events.map((ev) => (
            <FeedItem key={`${ev.id}-${ev.type}`} event={ev} />
          ))}
        </ul>
      )}
    </div>
  )
}

function FeedItem({ event }: { event: FeedEvent }) {
  const { order } = event
  const isBuy = order?.direction === 'BUY' || order?.direction === 'COVER'
  const time = new Date(event.timestamp).toLocaleTimeString()

  return (
    <li className={`feed-item ${event.type}`}>
      <span className="feed-time">{time}</span>
      {statusIcon[event.type]}
      <span className="feed-status">{statusLabel[event.type]}</span>
      {order?.symbol && (
        <>
          <span className="feed-symbol">{order.symbol}</span>
          {order.direction && (
            isBuy
              ? <TrendingUp size={12} className="dir-up" />
              : <TrendingDown size={12} className="dir-down" />
          )}
          {order.direction && (
            <span className={`feed-dir ${order.direction.toLowerCase()}`}>
              {order.direction}
            </span>
          )}
          {order.quantity && (
            <span className="feed-qty">{order.quantity.toLocaleString()}</span>
          )}
        </>
      )}
      {event.message && <span className="feed-msg">{event.message}</span>}
    </li>
  )
}
