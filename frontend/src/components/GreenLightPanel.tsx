import { useEffect, useState, type ReactNode } from 'react'
import {
  Brain, ChevronRight,
  Target, Shield, Scale, TrendingUp, TrendingDown,
} from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import type { StagedOrder } from '../types'
import type { BrainEvent } from './BrainActivityFeed'
import { PageHead } from './ui/primitives'
import { fmtUSD, relTime, confidenceColor, C } from '../lib/format'

interface Props {
  orders: StagedOrder[]
  onApprove: (signalId: string, comment: string) => Promise<void>
  onReject: (signalId: string, comment: string) => Promise<void>
}

/**
 * Green Light — card-per-staged-order layout (Nocturne template).
 * Approve/reject logic and trader comments are unchanged.
 */
export function GreenLightPanel({ orders, onApprove, onReject }: Props) {
  const [processing, setProcessing] = useState<string | null>(null)
  const [comments, setComments] = useState<Record<string, string>>({})

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

  return (
    <div className="flex flex-col gap-[22px]">
      <PageHead
        eyebrow="Green Light staging"
        title={orders.length === 0 ? 'Nothing waiting' : `${orders.length} order${orders.length === 1 ? '' : 's'} waiting on a human`}
        right={
          <span className="text-right font-mono text-[12px] leading-[1.5] text-ink-faint">
            auto-execute only inside the envelope
            <br />
            everything else waits here
          </span>
        }
      />

      <div className="grid grid-cols-1 items-start gap-4 xl:grid-cols-[1fr_400px]">
        {/* Order queue */}
        <div className="flex flex-col gap-3">
          {orders.length === 0 ? (
            <div className="mf-card flex flex-col items-center gap-2.5 p-14 text-center">
              <span className="text-[24px] font-medium">Queue clear</span>
              <span className="font-mono text-[13px] leading-[1.5] text-ink-faint">
                Nothing outside the envelope. The deterministic layer has the wheel.
              </span>
            </div>
          ) : (
            orders.map((order) => (
              <OrderCard
                key={order.id}
                order={order}
                comment={comments[order.id] ?? ''}
                onCommentChange={(c) => setComments((prev) => ({ ...prev, [order.id]: c }))}
                onApprove={() => handle(order.id, 'approve')}
                onReject={() => handle(order.id, 'reject')}
                isProcessing={processing === order.id}
              />
            ))
          )}
        </div>

        <AdvisoryPanel />
      </div>
    </div>
  )
}

// ─── Advisory (read-only brain output) ────────────────────────────────────────

function AdvisoryPanel() {
  const [events, setEvents] = useState<BrainEvent[]>([])

  useEffect(() => {
    fetch('/api/brain/activity')
      .then((r) => r.json())
      .then((d) => setEvents(d.events ?? []))
      .catch(() => {})
  }, [])

  const latest = events[0]

  return (
    <div className="mf-card flex flex-col gap-4 p-[22px]">
      <div className="flex items-center gap-2">
        <span className="mf-eyebrow">Brain activity · read-only</span>
        <span className="mf-tag-outline ml-auto">advisory</span>
      </div>
      <span className="font-mono text-[12px] leading-[1.5] text-ink-faint">
        advisory · read-only — no agent can place an order. Every order above was staged by the
        pipeline and waits for a human.
      </span>
      <div
        className="overflow-auto rounded-lg bg-base p-4"
        style={{ boxShadow: `0 0 0 1px ${C.line}` }}
      >
        {latest ? (
          <pre className="m-0 whitespace-pre-wrap font-mono text-[11.5px] leading-[1.7] text-[#cfd3e5]">
{JSON.stringify(
  {
    at: new Date(latest.timestamp).toISOString(),
    symbol: latest.symbol,
    step: latest.step,
    status: latest.status,
    detail: latest.detail,
  },
  null,
  2,
)}
          </pre>
        ) : (
          <span className="font-mono text-[11.5px] text-ink-faint">
            no brain activity yet — the brain posts every bar (~5 min) during market hours
          </span>
        )}
      </div>
      <span className="font-mono text-[12px] leading-[1.4] text-ink-muted">
        {events.length > 0
          ? `${events.length} recent steps in the ring buffer · latest ${relTime(latest.timestamp)} ago`
          : 'source · /api/brain/activity'}
      </span>
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
  const notional = order.limit_price > 0 ? order.quantity * order.limit_price : null
  const confidencePct = Math.round(order.confidence * 100)

  return (
    <div className="mf-card flex flex-col gap-4 p-[22px]">
      {/* Header */}
      <div className="flex flex-wrap items-center gap-3">
        <span className="font-mono text-[24px] font-medium leading-none">{order.symbol}</span>
        <span className="mf-tag-neutral">{order.strategy_name}</span>
        <span className={`mf-chip ${isBuy ? 'bg-signal-green/15 text-signal-green' : 'bg-signal-red/15 text-signal-red'}`}>
          {order.direction}
        </span>
        <span className="font-mono text-[12px] text-ink-faint">staged {relTime(order.created_at)} ago</span>
        <span className="ml-auto font-mono text-[12px]" style={{ color: confidenceColor(order.confidence) }}>
          confidence {confidencePct}%
        </span>
      </div>

      {/* 4-column grid */}
      <div className="grid grid-cols-2 gap-4 sm:grid-cols-4">
        <Field label="side · shares" value={`${order.direction} ${order.quantity.toLocaleString()}`} />
        <Field label="limit" value={order.limit_price > 0 ? fmtUSD(order.limit_price) : 'market'} />
        <Field label="notional" value={notional != null ? fmtUSD(notional, 0) : '—'} />
        <Field label="model" value={order.model_used || '—'} small />
      </div>

      {/* Reasoning */}
      <details className="group">
        <summary className="flex cursor-pointer list-none items-center gap-1.5 text-[12.5px] font-semibold text-ink-muted hover:text-ink">
          <Brain size={13} className="text-ink-faint" />
          Agent reasoning chain
          <ChevronRight size={12} className="text-ink-faint transition-transform group-open:rotate-90" />
        </summary>
        <div className="mt-3 flex flex-col gap-2">
          <ReasoningSection reasoning={order.reasoning} />
        </div>
      </details>

      {/* Comment + actions */}
      <input
        placeholder="Optional note — written to the audit trail…"
        value={comment}
        onChange={(e) => onCommentChange(e.target.value)}
        disabled={isProcessing}
        className="w-full rounded-lg bg-base px-2.5 py-2 text-xs text-ink outline-none disabled:opacity-50"
        style={{ boxShadow: `0 0 0 1px ${C.lineSoft}` }}
      />
      <div className="flex items-center gap-2.5">
        <span className="font-mono text-[12px] text-ink-faint">
          {isBuy ? 'buying power checked at send' : 'position checked at send'}
        </span>
        <button onClick={onReject} disabled={isProcessing} className="mf-btn-secondary ml-auto">
          Reject
        </button>
        <button onClick={onApprove} disabled={isProcessing} className="mf-btn-primary">
          {isProcessing ? 'Processing…' : 'Approve & send'}
        </button>
      </div>
    </div>
  )
}

function Field({ label, value, small = false }: { label: string; value: string; small?: boolean }) {
  return (
    <div className="flex flex-col gap-1">
      <span className="font-mono text-[11px] text-ink-faint">{label}</span>
      <span className={`tabular font-medium leading-none ${small ? 'font-mono text-[13px]' : 'text-[20px]'}`}>
        {value}
      </span>
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
    borderColor: '#968ae0',
    headerColor: '#c8bef6',
    bgColor: '#968ae008',
  },
  Bull: {
    icon: <TrendingUp size={12} />,
    label: 'Bull Case',
    borderColor: '#b5abfc',
    headerColor: '#cfc6fd',
    bgColor: '#b5abfc08',
  },
  Bear: {
    icon: <TrendingDown size={12} />,
    label: 'Bear Case',
    borderColor: '#d97b84',
    headerColor: '#e5a6ad',
    bgColor: '#d97b8408',
  },
  Judge: {
    icon: <Scale size={12} />,
    label: 'Judge',
    borderColor: '#968ae0',
    headerColor: '#d3c9fc',
    bgColor: '#968ae008',
  },
  Risk: {
    icon: <Shield size={12} />,
    label: 'Risk Manager',
    borderColor: '#d9a05b',
    headerColor: '#e7c188',
    bgColor: '#d9a05b08',
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
            <div key={tag} className="rounded-lg p-3 text-[12px] text-ink-muted" style={{ boxShadow: `0 0 0 1px ${C.lineSoft}` }}>
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
            <div className="text-[12px] leading-relaxed text-ink-muted">
              <ReactMarkdown>{content}</ReactMarkdown>
            </div>
          </div>
        )
      })}
    </>
  )
}
