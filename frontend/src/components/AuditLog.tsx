import { useState, useEffect, useCallback } from 'react'
import { ChevronDown, ChevronRight, ChevronLeft, RefreshCw } from 'lucide-react'

interface AuditEntry {
  id: number
  signal_id: string
  from_status: string
  to_status: string
  actor: string
  message: string
  timestamp: number
  symbol: string
  direction: string
  quantity: number
  limit_price: number
  confidence: number
  strategy_name: string
  reasoning: string
}

const STATUS_COLORS: Record<string, string> = {
  PENDING:  '#f59e0b',
  APPROVED: '#3b82f6',
  EXECUTED: '#22c55e',
  REJECTED: '#ef4444',
  FAILED:   '#ef4444',
  EXPIRED:  '#6b7280',
}

const PAGE_SIZES = [25, 50, 100]

// Plain-language explanation of what each transition actually means.
function explainTransition(e: AuditEntry): string {
  const t = `${e.from_status}→${e.to_status}`
  switch (t) {
    case '→PENDING':
    case 'PENDING→PENDING':
      return 'The AI brain proposed this trade and staged it. It waits here until you approve it (Green Light) or auto-execute picks it up. If neither happens, it eventually expires — no money moves.'
    case 'PENDING→APPROVED':
      return e.actor === 'auto-executor'
        ? 'Auto-execute was ON and this signal cleared the confidence bar, so the system approved it without manual review.'
        : 'You (or an operator) approved this trade via the Green Light gate.'
    case 'APPROVED→EXECUTED':
      return 'The order was sent to Alpaca and filled — this is the moment money actually moved.'
    case 'PENDING→REJECTED':
      return e.actor === 'auto-executor' || e.actor === 'system'
        ? 'The system rejected this signal (risk rules or expiry).'
        : 'You rejected this trade at the Green Light gate.'
    case 'APPROVED→FAILED':
    case 'PENDING→FAILED':
      return 'Execution failed — the order never reached the exchange. Check the message for the broker error.'
    default:
      if (e.to_status === 'PENDING') return 'Order staged by the AI brain — no execution yet.'
      return ''
  }
}

function statusBadge(status: string) {
  const color = STATUS_COLORS[status] ?? '#6b7280'
  return (
    <span style={{
      background: color + '22', color, border: `1px solid ${color}55`,
      borderRadius: 4, padding: '1px 7px', fontSize: 11, fontWeight: 700,
      letterSpacing: '0.04em',
    }}>
      {status}
    </span>
  )
}

export function AuditLog() {
  const [entries, setEntries] = useState<AuditEntry[]>([])
  const [total, setTotal] = useState(0)
  const [page, setPage] = useState(0)
  const [pageSize, setPageSize] = useState(50)
  const [expanded, setExpanded] = useState<number | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const load = useCallback(async () => {
    try {
      const res = await fetch(`/api/orders/audit-log?limit=${pageSize}&offset=${page * pageSize}`)
      if (!res.ok) throw new Error(`HTTP ${res.status}`)
      const data = await res.json()
      setEntries(data.entries ?? [])
      setTotal(data.total ?? 0)
      setError(null)
    } catch (e) {
      setError(String(e))
    } finally {
      setLoading(false)
    }
  }, [page, pageSize])

  useEffect(() => {
    load()
    const iv = setInterval(load, 30_000)
    return () => clearInterval(iv)
  }, [load])

  const pageCount = Math.max(1, Math.ceil(total / pageSize))
  const from = total === 0 ? 0 : page * pageSize + 1
  const to = Math.min(total, (page + 1) * pageSize)

  return (
    <div style={{ padding: '0 0 24px' }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 6 }}>
        <h2 style={{ margin: 0, fontSize: 16, fontWeight: 700, color: '#e2e8f0' }}>
          Audit Events
        </h2>
        <button
          onClick={load}
          style={{
            background: 'transparent', border: '1px solid #334155', color: '#94a3b8',
            borderRadius: 6, padding: '4px 12px', cursor: 'pointer', fontSize: 12,
            display: 'flex', alignItems: 'center', gap: 5,
          }}
        >
          <RefreshCw size={11} /> Refresh
        </button>
      </div>
      <p style={{ margin: '0 0 14px', fontSize: 12, color: '#64748b', lineHeight: 1.6 }}>
        Every state change of every order. <b style={{ color: '#94a3b8' }}>Click a row</b> to see the trade
        details, the AI's reasoning, and what the transition means. Lifecycle:{' '}
        {statusBadge('PENDING')} <span style={{ color: '#475569' }}>staged, no money moved</span>{' '}
        → {statusBadge('APPROVED')} <span style={{ color: '#475569' }}>cleared to trade</span>{' '}
        → {statusBadge('EXECUTED')} <span style={{ color: '#475569' }}>filled on Alpaca</span>.
      </p>

      {loading && <p style={{ color: '#64748b', fontSize: 14 }}>Loading…</p>}
      {error && <p style={{ color: '#ef4444', fontSize: 14 }}>Error: {error}</p>}
      {!loading && !error && entries.length === 0 && (
        <p style={{ color: '#64748b', fontSize: 14 }}>No audit events yet.</p>
      )}

      {entries.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12, color: '#cbd5e1' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid #1e293b', color: '#64748b', textAlign: 'left' }}>
                <th style={{ padding: '6px 6px', width: 20 }} />
                <th style={{ padding: '6px 10px', fontWeight: 600 }}>Time</th>
                <th style={{ padding: '6px 10px', fontWeight: 600 }}>Symbol</th>
                <th style={{ padding: '6px 10px', fontWeight: 600 }}>Trade</th>
                <th style={{ padding: '6px 10px', fontWeight: 600 }}>Transition</th>
                <th style={{ padding: '6px 10px', fontWeight: 600 }}>Actor</th>
                <th style={{ padding: '6px 10px', fontWeight: 600 }}>Message</th>
              </tr>
            </thead>
            <tbody>
              {entries.map((e) => {
                const isOpen = expanded === e.id
                const explanation = explainTransition(e)
                return (
                  <>
                    <tr
                      key={e.id}
                      onClick={() => setExpanded(isOpen ? null : e.id)}
                      style={{ borderBottom: isOpen ? 'none' : '1px solid #0f172a', cursor: 'pointer' }}
                      onMouseEnter={(ev) => (ev.currentTarget.style.background = '#161d2d')}
                      onMouseLeave={(ev) => (ev.currentTarget.style.background = 'transparent')}
                    >
                      <td style={{ padding: '6px 6px', color: '#475569' }}>
                        {isOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
                      </td>
                      <td style={{ padding: '6px 10px', whiteSpace: 'nowrap', color: '#64748b' }}>
                        {new Date(e.timestamp).toLocaleString()}
                      </td>
                      <td style={{ padding: '6px 10px', fontWeight: 700, color: '#93c5fd', whiteSpace: 'nowrap' }}>
                        {e.symbol || '—'}
                      </td>
                      <td style={{ padding: '6px 10px', whiteSpace: 'nowrap', color: '#94a3b8' }}>
                        {e.symbol ? (
                          <>
                            <b style={{ color: e.direction === 'BUY' ? '#22c55e' : '#ef4444' }}>{e.direction}</b>
                            {' '}{e.quantity.toLocaleString()}
                            {e.limit_price > 0 && <> @ ${e.limit_price.toFixed(2)}</>}
                            <span style={{ color: '#475569' }}> · conf {(e.confidence * 100).toFixed(0)}%</span>
                          </>
                        ) : '—'}
                      </td>
                      <td style={{ padding: '6px 10px', whiteSpace: 'nowrap' }}>
                        {e.from_status
                          ? <>{statusBadge(e.from_status)} <span style={{ color: '#475569', margin: '0 4px' }}>→</span> {statusBadge(e.to_status)}</>
                          : statusBadge(e.to_status)}
                      </td>
                      <td style={{ padding: '6px 10px', color: '#94a3b8', whiteSpace: 'nowrap' }}>{e.actor}</td>
                      <td style={{ padding: '6px 10px', color: '#64748b', maxWidth: 320, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {e.message}
                      </td>
                    </tr>
                    {isOpen && (
                      <tr key={`${e.id}-detail`} style={{ borderBottom: '1px solid #0f172a' }}>
                        <td colSpan={7} style={{ padding: '4px 10px 14px 36px' }}>
                          <div style={{
                            background: '#0d1117', border: '1px solid #1e293b', borderRadius: 10,
                            padding: '12px 16px', fontSize: 12.5, lineHeight: 1.7,
                          }}>
                            {explanation && (
                              <p style={{ margin: '0 0 8px', color: '#e2e8f0' }}>
                                <b style={{ color: '#a855f7' }}>What this means: </b>{explanation}
                              </p>
                            )}
                            <p style={{ margin: '0 0 8px', color: '#94a3b8' }}>
                              <b style={{ color: '#64748b' }}>Full message: </b>{e.message || '—'}
                              <span style={{ color: '#475569' }}> · Signal </span>
                              <span style={{ fontFamily: 'monospace', color: '#64748b' }}>{e.signal_id}</span>
                              {e.strategy_name && <span style={{ color: '#475569' }}> · Strategy {e.strategy_name}</span>}
                            </p>
                            {e.reasoning && (
                              <details>
                                <summary style={{ color: '#3b82f6', cursor: 'pointer', fontSize: 12 }}>
                                  AI reasoning (signal · bull · bear · judge · risk)
                                </summary>
                                <pre style={{
                                  whiteSpace: 'pre-wrap', color: '#94a3b8', fontSize: 11.5,
                                  fontFamily: 'inherit', margin: '8px 0 0', maxHeight: 260, overflowY: 'auto',
                                }}>
                                  {e.reasoning}
                                </pre>
                              </details>
                            )}
                          </div>
                        </td>
                      </tr>
                    )}
                  </>
                )
              })}
            </tbody>
          </table>
        </div>
      )}

      {/* Pagination */}
      {total > 0 && (
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between',
          marginTop: 14, flexWrap: 'wrap', gap: 10,
        }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, fontSize: 12, color: '#64748b' }}>
            Rows per page:
            {PAGE_SIZES.map((s) => (
              <button
                key={s}
                onClick={() => { setPageSize(s); setPage(0) }}
                style={{
                  background: pageSize === s ? '#1e3a5f' : 'transparent',
                  color: pageSize === s ? '#60a5fa' : '#64748b',
                  border: `1px solid ${pageSize === s ? '#3b82f640' : '#334155'}`,
                  borderRadius: 6, padding: '3px 10px', cursor: 'pointer', fontSize: 12, fontWeight: 600,
                }}
              >
                {s}
              </button>
            ))}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 10, fontSize: 12, color: '#64748b' }}>
            <span>{from}–{to} of {total.toLocaleString()}</span>
            <button
              onClick={() => setPage((p) => Math.max(0, p - 1))}
              disabled={page === 0}
              style={{
                background: 'transparent', border: '1px solid #334155',
                color: page === 0 ? '#334155' : '#94a3b8', borderRadius: 6,
                padding: '3px 8px', cursor: page === 0 ? 'default' : 'pointer',
                display: 'flex', alignItems: 'center',
              }}
            >
              <ChevronLeft size={13} />
            </button>
            <span>Page {page + 1} / {pageCount}</span>
            <button
              onClick={() => setPage((p) => Math.min(pageCount - 1, p + 1))}
              disabled={page >= pageCount - 1}
              style={{
                background: 'transparent', border: '1px solid #334155',
                color: page >= pageCount - 1 ? '#334155' : '#94a3b8', borderRadius: 6,
                padding: '3px 8px', cursor: page >= pageCount - 1 ? 'default' : 'pointer',
                display: 'flex', alignItems: 'center',
              }}
            >
              <ChevronRight size={13} />
            </button>
          </div>
        </div>
      )}
    </div>
  )
}
