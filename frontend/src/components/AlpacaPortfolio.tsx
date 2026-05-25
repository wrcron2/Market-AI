import { useState, useEffect, useCallback } from 'react'
import { TrendingUp, TrendingDown, RefreshCw, AlertTriangle } from 'lucide-react'
import {
  ResponsiveContainer,
  LineChart, Line,
  BarChart, Bar,
  XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine, Cell,
} from 'recharts'
import type { AlpacaAccount, AlpacaPosition, TradingLimits, Position, PortfolioSummary, StagedOrder } from '../types'

const REFRESH_MS = 30_000

interface Props {
  llmAlert: string | null
  onClearAlert: () => void
}

function fmtDate(unixMs: number): string {
  return new Date(unixMs).toLocaleString('en-US', {
    month: 'short', day: 'numeric', year: 'numeric',
    hour: '2-digit', minute: '2-digit',
  })
}

function fmtShortDate(unixMs: number): string {
  return new Date(unixMs).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

export function AlpacaPortfolio({ llmAlert, onClearAlert }: Props) {
  const [account,      setAccount]      = useState<AlpacaAccount | null>(null)
  const [positions,    setPositions]    = useState<AlpacaPosition[]>([])
  const [dbPositions,  setDbPositions]  = useState<Position[]>([])
  const [allPositions, setAllPositions] = useState<Position[]>([])
  const [limits,       setLimits]       = useState<TradingLimits | null>(null)
  const [summary,      setSummary]      = useState<PortfolioSummary | null>(null)
  const [failedOrders, setFailedOrders] = useState<StagedOrder[]>([])
  const [loading,      setLoading]      = useState(true)
  const [error,        setError]        = useState<string | null>(null)
  const [lastRefresh,  setLastRefresh]  = useState<Date | null>(null)
  const [selling,      setSelling]      = useState<string | null>(null)
  const [retrying,     setRetrying]     = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [acctRes, posRes, dbPosRes, limRes, histRes, sumRes, failRes] = await Promise.all([
        fetch('/api/alpaca/account'),
        fetch('/api/alpaca/positions'),
        fetch('/api/positions'),
        fetch('/api/trading/limits'),
        fetch('/api/positions/history'),
        fetch('/api/portfolio/summary'),
        fetch('/api/orders/failed'),
      ])
      if (acctRes.ok)   setAccount(await acctRes.json())
      if (posRes.ok)    setPositions(await posRes.json())
      if (dbPosRes.ok)  setDbPositions((await dbPosRes.json()).positions ?? [])
      if (limRes.ok)    setLimits(await limRes.json())
      if (histRes.ok)   setAllPositions((await histRes.json()).positions ?? [])
      if (sumRes.ok)    setSummary(await sumRes.json())
      if (failRes.ok)   setFailedOrders((await failRes.json()).orders ?? [])
      setError(null)
      setLastRefresh(new Date())
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Failed to load portfolio data')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    refresh()
    const interval = setInterval(refresh, REFRESH_MS)
    return () => clearInterval(interval)
  }, [refresh])

  const sellPosition = async (symbol: string) => {
    if (selling) return
    setSelling(symbol)
    try {
      const res = await fetch(`/api/alpaca/positions/${symbol}/close`, { method: 'POST' })
      if (!res.ok) throw new Error(await res.text())
      await refresh()
    } catch (e: any) {
      setError(`Sell ${symbol} failed: ${e.message}`)
    } finally {
      setSelling(null)
    }
  }

  const retryOrder = async (signalId: string) => {
    if (retrying) return
    setRetrying(signalId)
    try {
      const res = await fetch('/api/orders/retry', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ signal_id: signalId }),
      })
      if (!res.ok) throw new Error(await res.text())
      await refresh()
    } catch (e: any) {
      setError(`Retry failed: ${e.message}`)
    } finally {
      setRetrying(null)
    }
  }

  const equity       = parseFloat(account?.equity ?? '0')
  const lastEquity   = parseFloat(account?.last_equity ?? '0')
  const dayPnl       = equity - lastEquity
  const buyingPower  = parseFloat(account?.buying_power ?? '0')
  const portfolioVal = parseFloat(account?.portfolio_value ?? '0')
  const allTimePnl   = summary?.all_time_realized_pnl ?? 0

  // Build symbol → entry_time map from history for the "Bought" column
  const symbolEntryTime: Record<string, number> = {}
  for (const p of allPositions) {
    if (p.status === 'OPEN' && !symbolEntryTime[p.symbol]) {
      symbolEntryTime[p.symbol] = p.entry_time
    }
  }

  // ── Chart data ──────────────────────────────────────────────────────────────

  // Closed positions: cumulative P&L line chart
  const closedSorted = [...allPositions]
    .filter(p => p.status === 'CLOSED' && p.exit_time != null)
    .sort((a, b) => (a.exit_time ?? 0) - (b.exit_time ?? 0))

  let cumulative = 0
  const cumulativeData = closedSorted.map(p => {
    cumulative += p.realized_pnl ?? 0
    return { date: fmtShortDate(p.exit_time!), cumPnl: parseFloat(cumulative.toFixed(2)), symbol: p.symbol }
  })

  // Open positions: unrealized P&L bar chart (always visible when positions exist)
  const openBarData = positions.map(p => ({
    name: p.symbol,
    pnl:  parseFloat(parseFloat(p.unrealized_pl).toFixed(2)),
  }))

  // Closed trades P&L per trade
  const tradeBarData = closedSorted.map(p => ({
    name: p.symbol,
    pnl:  parseFloat((p.realized_pnl ?? 0).toFixed(2)),
    date: fmtShortDate(p.exit_time!),
  }))

  if (loading) {
    return (
      <div className="alpaca-portfolio loading">
        <RefreshCw size={20} className="spin" />
        <span>Connecting to Alpaca paper account…</span>
      </div>
    )
  }

  if (error) {
    return (
      <div className="alpaca-portfolio error">
        <AlertTriangle size={20} />
        <span>{error}</span>
        <button style={{ marginLeft: 12, background: 'none', border: '1px solid #4a5080', color: '#8b8fa8', padding: '4px 10px', borderRadius: 6, cursor: 'pointer' }} onClick={() => setError(null)}>Dismiss</button>
      </div>
    )
  }

  return (
    <div className="alpaca-portfolio">
      {llmAlert && (
        <div className="llm-alert">
          <AlertTriangle size={16} />
          <span>Position monitor LLM unreachable — all positions on HOLD. {llmAlert}</span>
          <button className="llm-alert-close" onClick={onClearAlert}>✕</button>
        </div>
      )}
      {limits?.is_halted && (
        <div className="llm-alert halt-alert">
          <AlertTriangle size={16} />
          <span>Daily loss limit reached — new BUY orders are paused for today.</span>
        </div>
      )}

      {/* Account stats */}
      <div className="alpaca-stats-row">
        <AccountStat label="Portfolio Value" value={`$${portfolioVal.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`} />
        <AccountStat label="Equity" value={`$${equity.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`} />
        <AccountStat label="Buying Power" value={`$${buyingPower.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`} />
        <AccountStat label="Today's P&L" value={`${dayPnl >= 0 ? '+' : ''}$${dayPnl.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`} className={dayPnl >= 0 ? 'pnl-positive' : 'pnl-negative'} />
        <AccountStat label="Trades Today" value={String(limits?.trade_count ?? 0)} />
        <AccountStat label="Realized P&L Today" value={`${(limits?.realized_pnl ?? 0) >= 0 ? '+' : ''}$${(limits?.realized_pnl ?? 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`} className={(limits?.realized_pnl ?? 0) >= 0 ? 'pnl-positive' : 'pnl-negative'} />
        <AccountStat label="Total Earnings" value={`${allTimePnl >= 0 ? '+' : ''}$${allTimePnl.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`} className={allTimePnl >= 0 ? 'pnl-positive' : 'pnl-negative'} />
      </div>

      <div className="alpaca-refresh-row">
        <RefreshCw size={11} />
        {lastRefresh ? `Last synced ${lastRefresh.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}` : 'Syncing…'}
        <button className="refresh-btn" onClick={refresh}>Refresh</button>
      </div>

      {/* ── Open Positions ─────────────────────────────────────────────────────── */}
      <h3 className="alpaca-section-title">
        Open Positions
        <span className="badge">{positions.length + dbPositions.filter(p => p.status === 'OPEN' && !positions.find(ap => ap.symbol === p.symbol)).length}</span>
      </h3>

      {positions.length === 0 && dbPositions.filter(p => p.status === 'OPEN').length === 0 ? (
        <div className="alpaca-empty">No open positions</div>
      ) : (
        <div className="positions-table-wrap">
          <table className="positions-table">
            <thead>
              <tr>
                <th>Symbol</th>
                <th>Side</th>
                <th>Qty</th>
                <th>Bought</th>
                <th>Entry</th>
                <th>Current</th>
                <th>Mkt Value</th>
                <th>Unrealized P&L</th>
                <th>P&L %</th>
                <th>Status</th>
                <th>Sell</th>
              </tr>
            </thead>
            <tbody>
              {positions.map((pos) => {
                const plpc    = parseFloat(pos.unrealized_plpc) * 100
                const pl      = parseFloat(pos.unrealized_pl)
                const isLong  = pos.side === 'long'
                const plColor = pl >= 0 ? 'pnl-positive' : 'pnl-negative'
                const entryTs = symbolEntryTime[pos.symbol]
                const isSelling = selling === pos.symbol
                return (
                  <tr key={pos.symbol}>
                    <td className="pos-symbol">{pos.symbol}</td>
                    <td><span className={`direction-badge ${isLong ? 'buy' : 'sell'}`}>{isLong ? 'LONG' : 'SHORT'}</span></td>
                    <td>{parseFloat(pos.qty).toLocaleString()}</td>
                    <td className="text-muted pos-date">{entryTs ? fmtDate(entryTs) : '—'}</td>
                    <td>${parseFloat(pos.avg_entry_price).toFixed(2)}</td>
                    <td>${parseFloat(pos.current_price).toFixed(2)}</td>
                    <td>${parseFloat(pos.market_value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                    <td className={plColor}>{pl >= 0 ? '+' : ''}${pl.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                    <td className={plColor}>
                      <span className="plpc-cell">
                        {isLong ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
                        {plpc >= 0 ? '+' : ''}{plpc.toFixed(2)}%
                      </span>
                    </td>
                    <td><span className="close-reason-tag">filled</span></td>
                    <td>
                      <button
                        className="btn-sell"
                        onClick={() => sellPosition(pos.symbol)}
                        disabled={!!selling}
                        title={`Close ${pos.symbol} at market`}
                      >
                        {isSelling ? 'Closing…' : 'Sell'}
                      </button>
                    </td>
                  </tr>
                )
              })}
              {dbPositions
                .filter(p => p.status === 'OPEN' && !positions.find(ap => ap.symbol === p.symbol))
                .map((p) => (
                  <tr key={p.id} className="pending-row">
                    <td className="pos-symbol">{p.symbol}</td>
                    <td><span className={`direction-badge ${p.direction === 'LONG' ? 'buy' : 'sell'}`}>{p.direction}</span></td>
                    <td>{p.quantity.toLocaleString()}</td>
                    <td className="text-muted pos-date">{p.entry_time ? fmtDate(p.entry_time) : '—'}</td>
                    <td className="text-muted">{p.entry_price > 0 ? `$${p.entry_price.toFixed(2)}` : '—'}</td>
                    <td className="text-muted">—</td>
                    <td className="text-muted">—</td>
                    <td className="text-muted">—</td>
                    <td className="text-muted">—</td>
                    <td><span className="close-reason-tag pending-tag">pending fill</span></td>
                    <td>—</td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}

      {/* ── Performance Charts (open P&L always; cumulative only when closed trades exist) ── */}
      {(openBarData.length > 0 || cumulativeData.length > 0) && (
        <div className="charts-section">
          <h3 className="alpaca-section-title" style={{ marginTop: '32px' }}>Performance</h3>
          <div className="charts-row">

            {/* Open positions unrealized P&L — always visible */}
            {openBarData.length > 0 && (
              <div className="chart-card">
                <div className="chart-title">Unrealized P&L (Open Positions)</div>
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart data={openBarData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                    <XAxis dataKey="name" tick={{ fontSize: 11, fill: '#8b8fa8' }} />
                    <YAxis tick={{ fontSize: 11, fill: '#8b8fa8' }} tickFormatter={v => `$${v}`} width={60} />
                    <Tooltip
                      contentStyle={{ background: '#1a1f35', border: '1px solid #2a3050', borderRadius: 8 }}
                      labelStyle={{ color: '#c5c9e0' }}
                      formatter={(v) => [`$${Number(v).toFixed(2)}`, 'Unrealized P&L']}
                    />
                    <ReferenceLine y={0} stroke="#4a5080" />
                    <Bar dataKey="pnl" radius={[4, 4, 0, 0]}>
                      {openBarData.map((entry, i) => (
                        <Cell key={i} fill={entry.pnl >= 0 ? '#22c55e' : '#ef4444'} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}

            {/* Cumulative realized P&L over time — only when there's closed history */}
            {cumulativeData.length > 0 && (
              <div className="chart-card">
                <div className="chart-title">Cumulative Realized P&L</div>
                <ResponsiveContainer width="100%" height={220}>
                  <LineChart data={cumulativeData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                    <XAxis dataKey="date" tick={{ fontSize: 11, fill: '#8b8fa8' }} />
                    <YAxis tick={{ fontSize: 11, fill: '#8b8fa8' }} tickFormatter={v => `$${v}`} width={60} />
                    <Tooltip
                      contentStyle={{ background: '#1a1f35', border: '1px solid #2a3050', borderRadius: 8 }}
                      labelStyle={{ color: '#c5c9e0' }}
                      formatter={(v) => [`$${Number(v).toFixed(2)}`, 'Cum. P&L']}
                    />
                    <ReferenceLine y={0} stroke="#4a5080" />
                    <Line type="monotone" dataKey="cumPnl" stroke="#6c63ff" strokeWidth={2} dot={{ r: 3, fill: '#6c63ff' }} activeDot={{ r: 5 }} />
                  </LineChart>
                </ResponsiveContainer>
              </div>
            )}

            {/* Realized P&L per closed trade */}
            {tradeBarData.length > 0 && (
              <div className="chart-card">
                <div className="chart-title">P&L per Closed Trade</div>
                <ResponsiveContainer width="100%" height={220}>
                  <BarChart data={tradeBarData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                    <XAxis dataKey="name" tick={{ fontSize: 11, fill: '#8b8fa8' }} />
                    <YAxis tick={{ fontSize: 11, fill: '#8b8fa8' }} tickFormatter={v => `$${v}`} width={60} />
                    <Tooltip
                      contentStyle={{ background: '#1a1f35', border: '1px solid #2a3050', borderRadius: 8 }}
                      labelStyle={{ color: '#c5c9e0' }}
                      formatter={(v) => [`$${Number(v).toFixed(2)}`, 'Realized P&L']}
                    />
                    <ReferenceLine y={0} stroke="#4a5080" />
                    <Bar dataKey="pnl" radius={[4, 4, 0, 0]}>
                      {tradeBarData.map((entry, i) => (
                        <Cell key={i} fill={entry.pnl >= 0 ? '#22c55e' : '#ef4444'} />
                      ))}
                    </Bar>
                  </BarChart>
                </ResponsiveContainer>
              </div>
            )}
          </div>
        </div>
      )}

      {/* ── Closed Positions ───────────────────────────────────────────────────── */}
      {dbPositions.filter(p => p.status === 'CLOSED').length > 0 && (
        <>
          <h3 className="alpaca-section-title" style={{ marginTop: '24px' }}>
            Closed Today
            <span className="badge">{dbPositions.filter(p => p.status === 'CLOSED').length}</span>
          </h3>
          <div className="positions-table-wrap">
            <table className="positions-table">
              <thead>
                <tr>
                  <th>Symbol</th><th>Side</th><th>Bought</th><th>Entry</th><th>Exit</th><th>Realized P&L</th><th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {dbPositions.filter(p => p.status === 'CLOSED').map((p) => {
                  const pl = p.realized_pnl ?? 0
                  return (
                    <tr key={p.id} className="closed-row">
                      <td className="pos-symbol">{p.symbol}</td>
                      <td><span className={`direction-badge ${p.direction === 'LONG' ? 'buy' : 'sell'}`}>{p.direction}</span></td>
                      <td className="text-muted pos-date">{p.entry_time ? fmtDate(p.entry_time) : '—'}</td>
                      <td>${p.entry_price.toFixed(2)}</td>
                      <td>{p.exit_price != null ? `$${p.exit_price.toFixed(2)}` : '—'}</td>
                      <td className={pl >= 0 ? 'pnl-positive' : 'pnl-negative'}>{pl >= 0 ? '+' : ''}${pl.toFixed(2)}</td>
                      <td><span className="close-reason-tag">{p.close_reason || '—'}</span></td>
                    </tr>
                  )
                })}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* ── Failed Orders ──────────────────────────────────────────────────────── */}
      {failedOrders.length > 0 && (
        <>
          <h3 className="alpaca-section-title" style={{ marginTop: '24px', color: '#f87171' }}>
            Failed Orders
            <span className="badge" style={{ background: 'rgba(239,68,68,0.15)', color: '#f87171' }}>{failedOrders.length}</span>
          </h3>
          <div className="positions-table-wrap">
            <table className="positions-table">
              <thead>
                <tr>
                  <th>Symbol</th><th>Direction</th><th>Qty</th><th>Strategy</th><th>Confidence</th><th>Failed At</th><th></th>
                </tr>
              </thead>
              <tbody>
                {failedOrders.map(o => (
                  <tr key={o.id} className="failed-row">
                    <td className="pos-symbol">{o.symbol}</td>
                    <td><span className={`direction-badge ${o.direction === 'BUY' ? 'buy' : 'sell'}`}>{o.direction}</span></td>
                    <td>{o.quantity}</td>
                    <td className="text-muted">{o.strategy_name || '—'}</td>
                    <td>{(o.confidence * 100).toFixed(0)}%</td>
                    <td className="text-muted pos-date">{fmtDate(o.updated_at)}</td>
                    <td>
                      <button
                        className="btn-retry"
                        onClick={() => retryOrder(o.id)}
                        disabled={!!retrying}
                      >
                        {retrying === o.id ? 'Retrying…' : '↺ Retry'}
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </div>
  )
}

function AccountStat({ label, value, className = '' }: { label: string; value: string; className?: string }) {
  return (
    <div className="alpaca-stat-card">
      <div className={`alpaca-stat-value ${className}`}>{value}</div>
      <div className="alpaca-stat-label">{label}</div>
    </div>
  )
}
