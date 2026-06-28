import { TrendingUp, TrendingDown, Clock, Activity } from 'lucide-react'
import type { Position, AlpacaPosition } from '../types'

interface Props {
  positions: Position[]
  alpacaPositions: AlpacaPosition[]
}

function fmtTime(ms: number) {
  return new Date(ms).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function fmtUSD(n: number) {
  return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

const pnlCls = (v: number) => (v >= 0 ? 'text-emerald-400' : 'text-red-400')

export function TodaysTrades({ positions, alpacaPositions }: Props) {
  const todayStart = new Date()
  todayStart.setHours(0, 0, 0, 0)
  const todayMs = todayStart.getTime()

  const todayTrades = positions.filter(p => p.entry_time >= todayMs)

  if (todayTrades.length === 0) return null

  const alpacaMap: Record<string, AlpacaPosition> = {}
  for (const ap of alpacaPositions) alpacaMap[ap.symbol] = ap

  const totalPnl = todayTrades.reduce((sum, p) => {
    if (p.status === 'CLOSED') return sum + (p.realized_pnl ?? 0)
    const ap = alpacaMap[p.symbol]
    return sum + (ap ? parseFloat(ap.unrealized_pl) : 0)
  }, 0)

  return (
    <div>
      <div className="mb-3 flex items-center justify-between">
        <h3 className="flex items-center gap-2.5 text-base font-semibold">
          <Activity size={14} />
          Today's Trades
          <span className="rounded-full bg-surface-sunken px-2 py-0.5 text-[11px] font-semibold text-ink-muted">{todayTrades.length}</span>
        </h3>
        <div className={`font-mono text-[13px] font-semibold ${pnlCls(totalPnl)}`}>
          {totalPnl >= 0 ? '+' : ''}${fmtUSD(totalPnl)} today
        </div>
      </div>

      <div className="flex flex-col gap-2">
        {todayTrades.map(p => {
          const isLong = p.direction === 'LONG'
          const ap = alpacaMap[p.symbol]
          const isClosed = p.status === 'CLOSED'

          const pnl = isClosed
            ? (p.realized_pnl ?? 0)
            : ap ? parseFloat(ap.unrealized_pl) : null

          const currentPrice = isClosed
            ? p.exit_price
            : ap ? parseFloat(ap.current_price) : null

          const pnlPct = (pnl != null && p.entry_price > 0)
            ? (pnl / (p.entry_price * p.quantity)) * 100
            : null

          const Icon = isLong ? TrendingUp : TrendingDown

          return (
            <div
              key={p.id}
              className={`flex items-center justify-between gap-4 rounded-xl border p-3.5 ${
                isClosed ? 'border-line bg-surface' : 'border-line-soft bg-surface-raised'
              }`}
            >
              {/* Left: symbol + badges */}
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <Icon size={14} className={isLong ? 'text-emerald-400' : 'text-red-400'} />
                  <span className="font-mono text-sm font-bold">{p.symbol}</span>
                  <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                    isLong ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'
                  }`}>
                    {isLong ? 'LONG' : 'SHORT'}
                  </span>
                  <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                    isClosed ? 'bg-surface-sunken text-ink-faint' : 'bg-blue-500/15 text-blue-400'
                  }`}>
                    {isClosed ? 'closed' : 'open'}
                  </span>
                </div>
                <div className="mt-1 flex items-center gap-1.5 text-[11px] text-ink-faint">
                  <Clock size={11} />
                  {fmtTime(p.entry_time)}
                  {isClosed && p.exit_time && <> → {fmtTime(p.exit_time)}</>}
                  <span className="ml-1 text-ink-muted">{p.quantity.toLocaleString()} shares</span>
                  {p.close_reason && (
                    <span className="rounded bg-surface-sunken px-1.5 py-0.5 text-[10px] font-semibold text-ink-muted">{p.close_reason}</span>
                  )}
                </div>
              </div>

              {/* Middle: price journey */}
              <div className="flex items-center gap-2 text-[12px]">
                <div className="text-center">
                  <div className="text-[10px] text-ink-faint">entry</div>
                  <div className="font-mono font-semibold">${p.entry_price > 0 ? p.entry_price.toFixed(2) : '—'}</div>
                </div>
                <span className="text-ink-faint">→</span>
                <div className="text-center">
                  <div className="text-[10px] text-ink-faint">{isClosed ? 'exit' : 'now'}</div>
                  <div className="font-mono font-semibold">
                    {currentPrice != null ? `$${currentPrice.toFixed(2)}` : '—'}
                  </div>
                </div>
              </div>

              {/* Right: P&L */}
              <div className="text-right">
                {pnl != null ? (
                  <>
                    <div className={`font-mono text-sm font-bold ${pnlCls(pnl)}`}>
                      {pnl >= 0 ? '+' : ''}${fmtUSD(pnl)}
                    </div>
                    {pnlPct != null && (
                      <div className={`font-mono text-[11px] font-semibold ${pnlCls(pnl)}`}>
                        {pnl >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%
                      </div>
                    )}
                  </>
                ) : (
                  <div className="font-mono text-sm text-ink-faint">—</div>
                )}
                <div className="mt-0.5 text-[10px] text-ink-faint">
                  {(p.confidence * 100).toFixed(0)}% conf
                </div>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
