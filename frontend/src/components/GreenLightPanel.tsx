import { useState } from 'react'
import { CheckCircle, XCircle, TrendingUp, TrendingDown, Brain, AlertTriangle } from 'lucide-react'
import type { StagedOrder } from '../types'

interface Props {
  orders: StagedOrder[]
  onApprove: (signalId: string, comment: string) => Promise<void>
  onReject: (signalId: string, comment: string) => Promise<void>
}

/**
 * GreenLightPanel — the trader's approval queue.
 * Every staged order must pass through here before reaching IBKR.
 */
export function GreenLightPanel({ orders, onApprove, onReject }: Props) {
  const [processing, setProcessing] = useState<string | null>(null)
  const [comments, setComments] = useState<Record<string, string>>({})

  const handle = async (signalId: string, action: 'approve' | 'reject') => {
    setProcessing(signalId)
    try {
      const comment = comments[signalId] ?? ''
      if (action === 'approve') {
        await onApprove(signalId, comment)
      } else {
        await onReject(signalId, comment)
      }
    } finally {
      setProcessing(null)
    }
  }

  if (orders.length === 0) {
    return (
      <div className="green-light-panel empty">
        <CheckCircle size={40} className="empty-icon" />
        <p>No signals awaiting approval</p>
      </div>
    )
  }

  return (
    <div className="green-light-panel">
      <h2 className="panel-title">
        <AlertTriangle size={18} />
        Awaiting Green Light
        <span className="badge">{orders.length}</span>
      </h2>

      <div className="order-list">
        {orders.map((order) => (
          <OrderCard
            key={order.id}
            order={order}
            comment={comments[order.id] ?? ''}
            onCommentChange={(c) => setComments((prev) => ({ ...prev, [order.id]: c }))}
            onApprove={() => handle(order.id, 'approve')}
            onReject={() => handle(order.id, 'reject')}
            isProcessing={processing === order.id}
          />
        ))}
      </div>
    </div>
  )
}

// ─── Order Card ───────────────────────────────────────────────────────────────

interface CardProps {
  order: StagedOrder
  comment: string
  onCommentChange: (c: string) => void
  onApprove: () => void
  onReject: () => void
  isProcessing: boolean
}

function OrderCard({ order, comment, onCommentChange, onApprove, onReject, isProcessing }: CardProps) {
  const isBuy = order.direction === 'BUY' || order.direction === 'COVER'
  const confidencePct = Math.round(order.confidence * 100)
  const confidenceColor =
    order.confidence >= 0.95 ? '#22c55e' : order.confidence >= 0.90 ? '#eab308' : '#ef4444'

  return (
    <div className={`order-card ${isBuy ? 'buy' : 'sell'}`}>
      {/* Header */}
      <div className="order-header">
        <div className="order-symbol">
          {isBuy ? <TrendingUp size={16} /> : <TrendingDown size={16} />}
          <span className="symbol">{order.symbol}</span>
          <span className={`direction-badge ${order.direction.toLowerCase()}`}>
            {order.direction}
          </span>
        </div>
        <div className="order-meta">
          <span className="quantity">{order.quantity.toLocaleString()} shares</span>
          {order.limit_price > 0 && (
            <span className="price">@ ${order.limit_price.toFixed(2)}</span>
          )}
        </div>
      </div>

      {/* Confidence meter */}
      <div className="confidence-row">
        <span className="confidence-label">Confidence</span>
        <div className="confidence-bar-track">
          <div
            className="confidence-bar-fill"
            style={{ width: `${confidencePct}%`, background: confidenceColor }}
          />
        </div>
        <span className="confidence-pct" style={{ color: confidenceColor }}>
          {confidencePct}%
        </span>
      </div>

      {/* Strategy + model */}
      <div className="strategy-row">
        <Brain size={12} />
        <span>{order.strategy_name}</span>
        <span className="model-tag">{order.model_used}</span>
      </div>

      {/* AI Reasoning */}
      <details className="reasoning-details">
        <summary>AI Reasoning</summary>
        <p className="reasoning-text">{order.reasoning}</p>
      </details>

      {/* Trader comment */}
      <input
        className="comment-input"
        placeholder="Optional note..."
        value={comment}
        onChange={(e) => onCommentChange(e.target.value)}
        disabled={isProcessing}
      />

      {/* Action buttons */}
      <div className="action-buttons">
        <button
          className="btn btn-approve"
          onClick={onApprove}
          disabled={isProcessing}
        >
          <CheckCircle size={14} />
          {isProcessing ? 'Processing…' : 'Green Light ✅'}
        </button>
        <button
          className="btn btn-reject"
          onClick={onReject}
          disabled={isProcessing}
        >
          <XCircle size={14} />
          Reject
        </button>
      </div>
    </div>
  )
}
