import { useState, useMemo, type ReactNode } from 'react'
import {
  CheckCircle, XCircle, TrendingUp, TrendingDown, Brain, AlertTriangle,
  Search, Filter, ChevronLeft, ChevronRight, Calendar,
  Target, Shield, Scale,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import type { StagedOrder, Direction } from '../types'
import { Card, ConfidenceBar } from './ui/primitives'

interface Props {
  orders: StagedOrder[]
  onApprove: (signalId: string, comment: string) => Promise<void>
  onReject: (signalId: string, comment: string) => Promise<void>
}

const PAGE_SIZE = 10

export function GreenLightPanel({ orders, onApprove, onReject }: Props) {
  const [processing, setProcessing] = useState<string | null>(null)
  const [comments, setComments] = useState<Record<string, string>>({})

  const [search, setSearch] = useState('')
  const [direction, setDirection] = useState<Direction | 'ALL'>('ALL')
  const [confidence, setConfidence] = useState<'ALL' | 'HIGH' | 'MEDIUM' | 'LOW' | 'GTE_80'>('ALL')
  const [dateFrom, setDateFrom] = useState('')
  const [dateTo, setDateTo] = useState('')
  const [page, setPage] = useState(1)

  const handle = async (signalId: string, action: 'approve' | 'reject') => {
    setProcessing(signalId)
    try {
      const comment = comments[signalId] ?? ''
      if (action === 'approve') await onApprove(signalId, comment)
      else await onReject(signalId, comment)
    } finally {
      setProcessing(null)
    }
  }

  const filtered = useMemo(() => {
    return orders.filter((o) => {
      if (search && !o.symbol.toLowerCase().includes(search.toLowerCase())) return false
      if (direction !== 'ALL' && o.direction !== direction) return false
      if (confidence === 'HIGH' && o.confidence < 0.9) return false
      if (confidence === 'MEDIUM' && (o.confidence < 0.75 || o.confidence >= 0.9)) return false
      if (confidence === 'LOW' && o.confidence >= 0.75) return false
      if (confidence === 'GTE_80' && o.confidence < 0.8) return false
      if (dateFrom && o.created_at < new Date(dateFrom).getTime()) return false
      if (dateTo && o.created_at > new Date(dateTo).getTime() + 86_400_000) return false
      return true
    })
  }, [orders, search, direction, confidence, dateFrom, dateTo])

  const gte80Count = useMemo(() => orders.filter((o) => o.confidence >= 0.8).length, [orders])
  const totalPages = Math.max(1, Math.ceil(filtered.length / PAGE_SIZE))
  const currentPage = Math.min(page, totalPages)
  const paginated = filtered.slice((currentPage - 1) * PAGE_SIZE, currentPage * PAGE_SIZE)

  const resetFilters = () => {
    setSearch(''); setDirection('ALL'); setConfidence('ALL'); setDateFrom(''); setDateTo(''); setPage(1)
  }
  const hasFilters = search || direction !== 'ALL' || confidence !== 'ALL' || dateFrom || dateTo

  const selectCls =
    'rounded-lg border border-line-soft bg-base px-2.5 py-1.5 text-xs text-ink outline-none focus:border-signal-blue'

  if (orders.length === 0) {
    return (
      <Card className="flex flex-col items-center justify-center gap-3 py-16 text-ink-faint">
        <CheckCircle size={40} className="text-signal-green/60" />
        <p className="text-sm">No signals awaiting approval</p>
      </Card>
    )
  }

  return (
    <Card className="overflow-hidden">
      {/* Title */}
      <div className="flex items-center gap-2.5 border-b border-line-soft px-4 py-3.5">
        <AlertTriangle size={18} className="text-signal-yellow" />
        <span className="text-sm font-semibold">Green Light Queue</span>
        <span className="rounded-full bg-signal-yellow/16 px-2 py-0.5 text-[11px] font-semibold text-yellow-300">
          {orders.length} pending
        </span>
        {filtered.length !== orders.length && (
          <span className="text-[11px] text-ink-faint">{filtered.length} shown</span>
        )}
      </div>

      {/* Filter toolbar */}
      <div className="flex flex-wrap items-center gap-2 border-b border-line-soft px-4 py-2.5">
        <div className="flex items-center gap-1.5 rounded-lg border border-line-soft bg-base px-2.5 py-1.5">
          <Search size={13} className="text-ink-faint" />
          <input
            placeholder="Search symbol…"
            value={search}
            onChange={(e) => { setSearch(e.target.value); setPage(1) }}
            className="w-24 bg-transparent text-xs text-ink outline-none placeholder:text-slate-600"
          />
        </div>
        <div className="flex items-center gap-1.5">
          <Filter size={12} className="text-ink-faint" />
          <select value={direction} onChange={(e) => { setDirection(e.target.value as Direction | 'ALL'); setPage(1) }} className={selectCls}>
            <option value="ALL">All directions</option>
            <option value="BUY">BUY</option>
            <option value="SELL">SELL</option>
            <option value="SHORT">SHORT</option>
            <option value="COVER">COVER</option>
          </select>
        </div>
        <select value={confidence} onChange={(e) => { setConfidence(e.target.value as typeof confidence); setPage(1) }} className={selectCls}>
          <option value="ALL">All confidence</option>
          <option value="GTE_80">≥ 80% · {gte80Count}</option>
          <option value="HIGH">High ≥ 90%</option>
          <option value="MEDIUM">Medium 75–90%</option>
          <option value="LOW">Low &lt; 75%</option>
        </select>
        <div className="flex items-center gap-1.5">
          <Calendar size={12} className="text-ink-faint" />
          <input type="date" value={dateFrom} onChange={(e) => { setDateFrom(e.target.value); setPage(1) }} title="From date" className={selectCls} />
        </div>
        <div className="flex items-center gap-1.5">
          <span className="text-ink-faint">→</span>
          <input type="date" value={dateTo} onChange={(e) => { setDateTo(e.target.value); setPage(1) }} title="To date" className={selectCls} />
        </div>
        {hasFilters && (
          <button onClick={resetFilters} className="rounded-lg border border-line-soft px-2.5 py-1.5 text-xs text-ink-muted hover:text-ink">
            Clear
          </button>
        )}
      </div>

      {/* List */}
      {filtered.length === 0 ? (
        <div className="flex flex-col items-center gap-2 py-12 text-ink-faint">
          <Search size={24} />
          <p className="text-sm">No signals match your filters</p>
          <button onClick={resetFilters} className="text-xs text-signal-blue">Clear filters</button>
        </div>
      ) : (
        <>
          <div className="flex flex-col gap-3 p-4">
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

          {totalPages > 1 && (
            <div className="flex items-center gap-1.5 border-t border-line-soft px-4 py-2.5">
              <button
                onClick={() => setPage((p) => Math.max(1, p - 1))}
                disabled={currentPage === 1}
                className="flex h-7 w-7 items-center justify-center rounded-md border border-line-soft text-ink-muted disabled:opacity-40"
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
                    <span key={`e-${i}`} className="px-1 text-ink-faint">…</span>
                  ) : (
                    <button
                      key={p}
                      onClick={() => setPage(p as number)}
                      className={`flex h-7 min-w-7 items-center justify-center rounded-md border px-2 text-xs ${
                        currentPage === p
                          ? 'border-signal-blue bg-signal-blue/10 text-blue-200'
                          : 'border-line-soft text-ink-muted'
                      }`}
                    >
                      {p}
                    </button>
                  ),
                )}
              <button
                onClick={() => setPage((p) => Math.min(totalPages, p + 1))}
                disabled={currentPage === totalPages}
                className="flex h-7 w-7 items-center justify-center rounded-md border border-line-soft text-ink-muted disabled:opacity-40"
              >
                <ChevronRight size={14} />
              </button>
              <span className="ml-auto font-mono text-[11px] text-ink-faint">
                {(currentPage - 1) * PAGE_SIZE + 1}–{Math.min(currentPage * PAGE_SIZE, filtered.length)} of {filtered.length}
              </span>
            </div>
          )}
        </>
      )}
    </Card>
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
  return (
    d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' }) +
    ' · ' +
    d.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
  )
}

function OrderCard({ order, comment, onCommentChange, onApprove, onReject, isProcessing }: CardProps) {
  const isBuy = order.direction === 'BUY' || order.direction === 'COVER'
  const confidencePct = Math.round(order.confidence * 100)
  const confidenceColor =
    order.confidence >= 0.95 ? '#22c55e' : order.confidence >= 0.9 ? '#eab308' : '#ef4444'

  return (
    <div className="rounded-xl border border-line-soft bg-surface-raised p-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <span className={isBuy ? 'text-signal-green' : 'text-signal-red'}>
            {isBuy ? <TrendingUp size={16} /> : <TrendingDown size={16} />}
          </span>
          <span className="font-mono text-[17px] font-bold">{order.symbol}</span>
          <span className={`mf-chip ${isBuy ? 'bg-signal-green/15 text-emerald-400' : 'bg-signal-red/15 text-red-400'}`}>
            {order.direction}
          </span>
        </div>
        <div className="flex items-center gap-2 font-mono text-[12px] text-ink-muted">
          <span>{order.quantity.toLocaleString()} sh</span>
          {order.limit_price > 0 && <span>@ ${order.limit_price.toFixed(2)}</span>}
        </div>
      </div>

      {/* Date */}
      <div className="mt-2 flex items-center gap-1.5 font-mono text-[11px] text-ink-faint">
        <Calendar size={11} />
        {formatDate(order.created_at)}
      </div>

      {/* Confidence */}
      <div className="mt-3 flex items-center gap-2.5">
        <span className="w-[70px] text-[11px] text-ink-faint">Confidence</span>
        <ConfidenceBar value={order.confidence} className="flex-1" />
        <span className="w-10 text-right font-mono text-[13px] font-semibold" style={{ color: confidenceColor }}>
          {confidencePct}%
        </span>
      </div>

      {/* Strategy + model */}
      <div className="mt-3 flex items-center gap-2 text-[11px] text-ink-muted">
        <Brain size={12} className="text-ink-faint" />
        <span className="font-mono">{order.strategy_name}</span>
        <span className="rounded bg-surface-sunken px-1.5 py-0.5 font-mono text-[10px]">{order.model_used}</span>
      </div>

      {/* AI Reasoning */}
      <details className="group mt-3 border-t border-line-soft pt-3">
        <summary className="flex cursor-pointer list-none items-center gap-1.5 text-[12.5px] font-semibold text-ink-muted hover:text-ink">
          <Brain size={13} className="text-ink-faint" />
          Agent Reasoning Chain
          <span className="ml-auto text-[10px] font-normal text-ink-faint group-open:hidden">click to expand</span>
        </summary>
        <div className="mt-3 flex flex-col gap-2">
          <ReasoningSection reasoning={order.reasoning} />
        </div>
      </details>

      {/* Comment */}
      <input
        placeholder="Optional note…"
        value={comment}
        onChange={(e) => onCommentChange(e.target.value)}
        disabled={isProcessing}
        className="mt-3 w-full rounded-lg border border-line-soft bg-base px-2.5 py-2 text-xs text-ink outline-none focus:border-signal-blue disabled:opacity-50"
      />

      {/* Actions */}
      <div className="mt-2.5 flex gap-2">
        <button
          onClick={onApprove}
          disabled={isProcessing}
          className="flex flex-1 items-center justify-center gap-1.5 rounded-lg bg-signal-green py-2.5 text-[13px] font-bold text-[#06210f] hover:bg-green-600 hover:text-white disabled:opacity-60"
        >
          <CheckCircle size={14} />
          {isProcessing ? 'Processing…' : 'Approve'}
        </button>
        <button
          onClick={onReject}
          disabled={isProcessing}
          className="flex flex-1 items-center justify-center gap-1.5 rounded-lg border border-signal-red bg-signal-red/10 py-2.5 text-[13px] font-bold text-red-300 hover:bg-signal-red hover:text-white disabled:opacity-60"
        >
          <XCircle size={14} />
          Reject
        </button>
      </div>
    </div>
  )
}

// ─── Reasoning Sections ───────────────────────────────────────────────────────

interface ReasoningBlock {
  tag: string
  content: string
}

const REASONING_META: Record<string, {
  icon: ReactNode
  label: string
  borderColor: string
  headerColor: string
  bgColor: string
}> = {
  Signal: {
    icon: <Target size={12} />,
    label: 'Signal Generator',
    borderColor: '#3b82f6',
    headerColor: '#93c5fd',
    bgColor: '#3b82f608',
  },
  Bull: {
    icon: <TrendingUp size={12} />,
    label: 'Bull Case',
    borderColor: '#22c55e',
    headerColor: '#86efac',
    bgColor: '#22c55e08',
  },
  Bear: {
    icon: <TrendingDown size={12} />,
    label: 'Bear Case',
    borderColor: '#ef4444',
    headerColor: '#fca5a5',
    bgColor: '#ef444408',
  },
  Judge: {
    icon: <Scale size={12} />,
    label: 'Judge',
    borderColor: '#a855f7',
    headerColor: '#d8b4fe',
    bgColor: '#a855f708',
  },
  Risk: {
    icon: <Shield size={12} />,
    label: 'Risk Manager',
    borderColor: '#f59e0b',
    headerColor: '#fcd34d',
    bgColor: '#f59e0b08',
  },
}

function parseReasoning(text: string): ReasoningBlock[] {
  const tags = ['Signal', 'Bull', 'Bear', 'Judge', 'Risk']
  const result: ReasoningBlock[] = []

  for (let i = 0; i < tags.length; i++) {
    const tag = tags[i]
    const marker = `[${tag}]`
    const startIdx = text.indexOf(marker)
    if (startIdx === -1) continue

    const contentStart = startIdx + marker.length
    let endIdx = text.length
    for (let j = i + 1; j < tags.length; j++) {
      const nextIdx = text.indexOf(`[${tags[j]}]`, contentStart)
      if (nextIdx !== -1 && nextIdx < endIdx) endIdx = nextIdx
    }
    result.push({ tag, content: text.slice(contentStart, endIdx).trim() })
  }

  if (result.length === 0) {
    return [{ tag: 'Analysis', content: text.trim() }]
  }
  return result
}

function ReasoningSection({ reasoning }: { reasoning: string }) {
  const blocks = parseReasoning(reasoning)

  return (
    <>
      {blocks.map(({ tag, content }) => {
        const meta = REASONING_META[tag]
        if (!meta) {
          return (
            <div key={tag} className="rounded-lg border border-line-soft p-3 text-[12px] text-ink-muted">
              <div className="prose-mf leading-relaxed"><ReactMarkdown>{content}</ReactMarkdown></div>
            </div>
          )
        }
        return (
          <div
            key={tag}
            style={{
              borderLeft: `3px solid ${meta.borderColor}`,
              background: meta.bgColor,
            }}
            className="rounded-r-lg py-2 pl-3 pr-3"
          >
            <div className="mb-1.5 flex items-center gap-1.5" style={{ color: meta.headerColor }}>
              {meta.icon}
              <span className="text-[11px] font-bold uppercase tracking-wider">{meta.label}</span>
            </div>
            <div className="text-[12px] leading-relaxed text-slate-300">
              <ReactMarkdown>{content}</ReactMarkdown>
            </div>
          </div>
        )
      })}
    </>
  )
}
