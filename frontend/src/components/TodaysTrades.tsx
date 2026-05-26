import { TrendingUp, TrendingDown, Clock, Activity } from 'lucide-react'
import type { Position, AlpacaPosition } from '../types'

interface Props {
  positions: Position[]
  alpacaPositions: AlpacaPosition[]  // for live unrealized P&L on open trades
}

function fmtTime(ms: number) {
  return new Date(ms).toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' })
}

function fmtUSD(n: number) {
  return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

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
    <div className="todays-trades">
      <div className="todays-trades-header">
        <h3 className="alpaca-section-title" style={{ margin: 0 }}>
          <Activity size={14} />
          Today's Trades
          <span className="badge">{todayTrades.length}</span>
        </h3>
        <div className={`todays-trades-total ${totalPnl >= 0 ? 'pnl-positive' : 'pnl-negative'}`}>
          {totalPnl >= 0 ? '+' : ''}${fmtUSD(totalPnl)} today
        </div>
      </div>

      <div className="todays-trades-list">
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
            <div key={p.id} className={`trade-card ${isClosed ? 'trade-closed' : 'trade-open'}`}>

              {/* Left: symbol + badges */}
              <div className="trade-card-left">
                <div className="trade-symbol-row">
                  <Icon size={14} className={isLong ? 'text-green' : 'text-red'} />
                  <span className="trade-symbol">{p.symbol}</span>
                  <span className={`direction-badge ${isLong ? 'buy' : 'sell'}`}>
                    {isLong ? 'LONG' : 'SHORT'}
                  </span>
                  <span className={`trade-status-pill ${isClosed ? 'pill-closed' : 'pill-open'}`}>
                    {isClosed ? 'closed' : 'open'}
                  </span>
                </div>
                <div className="trade-meta">
                  <Clock size={11} />
                  {fmtTime(p.entry_time)}
                  {isClosed && p.exit_time && <> → {fmtTime(p.exit_time)}</>}
                  <span className="trade-qty">{p.quantity.toLocaleString()} shares</span>
                  {p.close_reason && (
                    <span className="close-reason-tag">{p.close_reason}</span>
                  )}
                </div>
              </div>

              {/* Middle: price journey */}
              <div className="trade-card-prices">
                <div className="trade-price-row">
                  <span className="trade-price-label">entry</span>
                  <span className="trade-price-val">${p.entry_price > 0 ? p.entry_price.toFixed(2) : '—'}</span>
                </div>
                <span className="trade-price-arrow">→</span>
                <div className="trade-price-row">
                  <span className="trade-price-label">{isClosed ? 'exit' : 'now'}</span>
                  <span className="trade-price-val">
                    {currentPrice != null ? `$${currentPrice.toFixed(2)}` : '—'}
                  </span>
                </div>
              </div>

              {/* Right: P&L */}
              <div className="trade-card-pnl">
                {pnl != null ? (
                  <>
                    <div className={`trade-pnl-amount ${pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}`}>
                      {pnl >= 0 ? '+' : ''}${fmtUSD(pnl)}
                    </div>
                    {pnlPct != null && (
                      <div className={`trade-pnl-pct ${pnl >= 0 ? 'pnl-positive' : 'pnl-negative'}`}>
                        {pnl >= 0 ? '+' : ''}{pnlPct.toFixed(2)}%
                      </div>
                    )}
                  </>
                ) : (
                  <div className="trade-pnl-amount text-muted">—</div>
                )}
                <div className="trade-conf">
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
