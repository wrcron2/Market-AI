import { useState, useMemo } from 'react'
import { CheckCircle, XCircle, TrendingUp, TrendingDown, Brain, AlertTriangle, Search, Filter, ChevronLeft, ChevronRight, Calendar } from 'lucide-react'
import type { StagedOrder, Direction } from '../types'

interface Props {
  orders: StagedOrder[]
  onApprove: (signalId: string, comment: string) => Promise<void>
  onReject: (signalId: string, comment: string) => Promise<void>
}

const PAGE_SIZE = 10

export function GreenLightPanel({ orders, onApprove, onReject }: Props) {
  const [processing, setProcessing] = useState<string | null>(null)
  const [comments, setComments]     = useState<Record<string, string>>({})

  // ── Filter state ───────────────────────────────────────────────────────────
  const [search,     setSearch]     = useState('')
  const [direction,  setDirection]  = useState<Direction | 'ALL'>('ALL')
  const [confidence, setConfidence] = useState<'ALL' | 'HIGH' | 'MEDIUM' | 'LOW'>('ALL')
  const [dateFrom,   setDateFrom]   = useState('')
  const [dateTo,     setDateTo]     = useState('')
  const [page,       setPage]       = useState(1)

  const handle = async (signalId: string, action: 'approve' | 'reject') => {
    setProcessing(signalId)
    try {
      const comment = comments[signalId] ?? ''
      if (action === 'approve') await onApprove(signalId, comment)
      else                      await onReject(signalId, comment)
    } finally {
      setProcessing(null)
    }
  }

  // ── Filtering ──────────────────────────────────────────────────────────────
  const filtered = useMemo(() => {
    return orders.filter((o) => {
      if (search && !o.symbol.toLowerCase().includes(search.toLowerCase())) return false
      if (direction !== 'ALL' && o.direction !== direction) return false
      if (confidence === 'HIGH'   && o.confidence < 0.90) return false
      if (confidence === 'MEDIUM' && (o.confidence < 0.75 || o.confidence >= 0.90)) return false
      if (confidence === 'LOW'    && o.confidence >= 0.75) return false
      if (dateFrom) {
        const from = new Date(dateFrom).getTime()
        if (o.created_at < from) return false
      }
      if (dateTo) {
        const to = new Date(dateTo).getTime() + 86_400_000 // include full day
        if (o.created_at > to) return false
      }
      return true
    })
  }, [orders, search, direction, confidence, dateFrom, dateTo])

  const totalPages  = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const currentPage = Math.min(page, totalPages)
  const paginated   = filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE)

  const resetFilters = () => {
    setSearch(''); setDirection('ALL'); setConfidence('ALL')
    setDateFrom(''); setDateTo(''); setPage(1)
  }
  const hasFilters = search || direction !== 'ALL' || confidence !== 'ALL' || dateFrom || dateTo

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
      {/* Title */}
      <h2 className="panel-title">
        <AlertTriangle size={18} />
        Awaiting Green Light
        <span className="badge">{orders.length}</span>
        {filtered.length !== orders.length && (
          <span className="badge-filtered">{filtered.length} shown</span>
        )}
      </h2>

      {/* Filter toolbar */}
      <div className="filter-toolbar">
        {/* Search */}
        <div className="filter-search">
          <Search size={13} />
          <input
            placeholder="Search symbol…"
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1) }}
          />
        </div>

        {/* Direction */}
        <div className="filter-select-wrap">
          <Filter size={12} />
          <select value={direction} onChange={(e) => { setDirection(e.target.value as Direction | 'ALL'); setPage(1) }}>
            <option value="ALL">All directions</option>
            <option value="BUY">BUY</option>
            <option value="SELL">SELL</option>
            <option value="SHORT">SHORT</option>
            <option value="COVER">COVER</option>
          </select>
        </div>

        {/* Confidence */}
        <div className="filter-select-wrap">
          <select value={confidence} onChange={(e) => { setConfidence(e.target.value as typeof confidence); setPage(1) }}>
            <option value="ALL">All confidence</option>
            <option value="HIGH">High ≥ 90%</option>
            <option value="MEDIUM">Medium 75–90%</option>
            <option value="LOW">Low &lt; 75%</option>
          </select>
        </div>

        {/* Date from */}
        <div className="filter-date-wrap">
          <Calendar size={12} />
          <input type="date" value={dateFrom} onChange={(e) => { setDateFrom(e.target.value); setPage(1) }} title="From date" />
        </div>

        {/* Date to */}
        <div className="filter-date-wrap">
          <span className="date-sep">→</span>
          <input type="date" value={dateTo} onChange={(e) => { setDateTo(e.target.value); setPage(1) }} title="To date" />
        </div>

        {hasFilters && (
          <button className="filter-clear" onClick={resetFilters}>Clear</button>
        )}
      </div>

      {/* List */}
      {filtered.length === 0 ? (
        <div className="filter-empty">
          <Search size={24} />
          <p>No signals match your filters</p>
          <button className="filter-clear" onClick={resetFilters}>Clear filters</button>
        </div>
      ) : (
        <>
          <div className="order-list">
            {paginated.map((order) => (
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

          {/* Pagination */}
          {totalPages > 1 && (
            <div className="pagination">
              <button
                className="page-btn"
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={currentPage === 1}
              >
                <ChevronLeft size={14} />
              </button>

              {Array.from({ length: totalPages }, (_, i) => i + 1)
                .filter((p) => p === 1 || p === totalPages || Math.abs(p - currentPage) <= 1)
                .reduce<(number | '…')[]>((acc, p, i, arr) => {
                  if (i > 0 && (p as number) - (arr[i - 1] as number) > 1) acc.push('…')
                  acc.push(p)
                  return acc
                }, [])
                .map((p, i) =>
                  p === '…' ? (
                    <span key={`ellipsis-${i}`} className="page-ellipsis">…</span>
                  ) : (
                    <button
                      key={p}
                      className={`page-btn ${currentPage === p ? 'active' : ''}`}
                      onClick={() => setPage(p as number)}
                    >
                      {p}
                    </button>
                  )
                )}

              <button
                className="page-btn"
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={currentPage === totalPages}
              >
                <ChevronRight size={14} />
              </button>

              <span className="page-info">
                {(currentPage - 1) * PAGE_SIZE + 1}–{Math.min(currentPage * PAGE_SIZE, filtered.length)} of {filtered.length}
              </span>
            </div>
          )}
        </>
      )}
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

function formatDate(ms: number): string {
  const d = new Date(ms)
  return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) +
    ' · ' +
    d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
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

      {/* Date */}
      <div className="order-date">
        <Calendar size={11} />
        {formatDate(order.created_at)}
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
        <button className="btn btn-approve" onClick={onApprove} disabled={isProcessing}>
          <CheckCircle size={14} />
          {isProcessing ? 'Processing…' : 'Green Light ✅'}
        </button>
        <button className="btn btn-reject" onClick={onReject} disabled={isProcessing}>
          <XCircle size={14} />
          Reject
        </button>
      </div>
    </div>
  )
}
