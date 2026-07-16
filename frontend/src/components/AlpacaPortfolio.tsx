import { useState, useEffect, useCallback } from 'react'
import { TrendingUp, TrendingDown, RefreshCw, AlertTriangle } from 'lucide-react'
import { PerformanceHistory } from './PerformanceHistory'
import { TodaysTrades } from './TodaysTrades'
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
    month: 'short', day: 'numeric', year: 'numeric', hour: '2-digit', minute: '2-digit',
  })
}
function fmtShortDate(unixMs: number): string {
  return new Date(unixMs).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

const TH = 'px-4 py-2.5 text-left text-[10.5px] font-semibold uppercase tracking-wide text-ink-faint whitespace-nowrap'
const TD = 'px-4 py-3 text-[13px] whitespace-nowrap'
const dirChip = (buy: boolean) =>
  `mf-chip ${buy ? 'bg-signal-green/15 text-emerald-400' : 'bg-signal-red/15 text-red-400'}`
const pnlCls = (v: number) => (v >= 0 ? 'text-signal-green' : 'text-signal-red')
const tag = 'rounded bg-surface-sunken px-2 py-0.5 text-[10px] font-semibold text-ink-muted'
const sectionTitle = 'flex items-center gap-2.5 text-base font-semibold'
const badge = 'rounded-full bg-surface-sunken px-2 py-0.5 text-[11px] font-semibold text-ink-muted'

export function AlpacaPortfolio({ llmAlert, onClearAlert }: Props) {
  const [account, setAccount] = useState<AlpacaAccount | null>(null)
  const [positions, setPositions] = useState<AlpacaPosition[]>([])
  const [dbPositions, setDbPositions] = useState<Position[]>([])
  const [allPositions, setAllPositions] = useState<Position[]>([])
  const [limits, setLimits] = useState<TradingLimits | null>(null)
  const [summary, setSummary] = useState<PortfolioSummary | null>(null)
  const [failedOrders, setFailedOrders] = useState<StagedOrder[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null)
  const [selling, setSelling] = useState<string | null>(null)
  const [retrying, setRetrying] = useState<string | null>(null)
  const [equityHistory, setEquityHistory] = useState<{ timestamp: number; equity: number }[]>([])

  const refresh = useCallback(async () => {
    try {
      const [acctRes, posRes, dbPosRes, limRes, histRes, sumRes, failRes, eqRes] = await Promise.all([
        fetch('/api/alpaca/account'),
        fetch('/api/alpaca/positions'),
        fetch('/api/positions'),
        fetch('/api/trading/limits'),
        fetch('/api/positions/history'),
        fetch('/api/portfolio/summary'),
        fetch('/api/orders/failed'),
        fetch('/api/alpaca/equity-history?period=1M&timeframe=1D'),
      ])
      if (acctRes.ok) setAccount(await acctRes.json())
      if (posRes.ok) setPositions(await posRes.json())
      if (dbPosRes.ok) setDbPositions((await dbPosRes.json()).positions ?? [])
      if (limRes.ok) setLimits(await limRes.json())
      if (histRes.ok) setAllPositions((await histRes.json()).positions ?? [])
      if (sumRes.ok) setSummary(await sumRes.json())
      if (failRes.ok) setFailedOrders((await failRes.json()).orders ?? [])
      if (eqRes.ok) setEquityHistory((await eqRes.json()).points ?? [])
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

  const equity = parseFloat(account?.equity ?? '0')
  const lastEquity = parseFloat(account?.last_equity ?? '0')
  const dayPnl = equity - lastEquity
  const buyingPower = parseFloat(account?.buying_power ?? '0')
  const portfolioVal = parseFloat(account?.portfolio_value ?? '0')
  const cash = parseFloat(account?.cash ?? '0')
  const allTimePnl = summary?.all_time_realized_pnl ?? 0

  const symbolEntryTime: Record<string, number> = {}
  for (const p of allPositions) {
    if (p.status === 'OPEN' && !symbolEntryTime[p.symbol]) symbolEntryTime[p.symbol] = p.entry_time
  }

  const closedSorted = [...allPositions]
    .filter((p) => p.status === 'CLOSED' && p.exit_time != null)
    .sort((a, b) => (a.exit_time ?? 0) - (b.exit_time ?? 0))

  let cumulative = 0
  const cumulativeData = closedSorted.map((p) => {
    cumulative += p.realized_pnl ?? 0
    return { date: fmtShortDate(p.exit_time!), cumPnl: parseFloat(cumulative.toFixed(2)), symbol: p.symbol }
  })
  const openBarData = positions.map((p) => ({ name: p.symbol, pnl: parseFloat(parseFloat(p.unrealized_pl).toFixed(2)) }))
  const tradeBarData = closedSorted.map((p) => ({
    name: p.symbol, pnl: parseFloat((p.realized_pnl ?? 0).toFixed(2)), date: fmtShortDate(p.exit_time!),
  }))
  // Alpaca's daily portfolio history reports each day's CLOSING equity, so its
  // last point lags the live account by up to a full day. Append the current
  // equity as a "Now" point so the line actually ends at what the account is
  // worth this moment — not yesterday's close.
  const baselineEquity = equityHistory[0]?.equity ?? 100_000
  const equityChartData: { date: string; equity: number; isLive: boolean }[] = equityHistory.map((pt) => ({
    date: new Date(pt.timestamp).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
    equity: parseFloat(pt.equity.toFixed(2)),
    isLive: false,
  }))
  if (account && equity > 0) {
    equityChartData.push({ date: 'Now', equity: parseFloat(equity.toFixed(2)), isLive: true })
  }

  if (loading) {
    return (
      <div className="flex items-center gap-2.5 rounded-xl border border-line bg-surface px-4 py-6 text-sm text-ink-muted">
        <RefreshCw size={18} className="animate-spin" />
        <span>Connecting to Alpaca paper account…</span>
      </div>
    )
  }

  if (error) {
    return (
      <div className="flex items-center gap-2.5 rounded-xl border border-signal-red/30 bg-signal-red/10 px-4 py-4 text-sm text-red-300">
        <AlertTriangle size={20} />
        <span>{error}</span>
        <button
          className="ml-3 rounded-md border border-line px-2.5 py-1 text-ink-muted hover:text-ink"
          onClick={() => setError(null)}
        >
          Dismiss
        </button>
      </div>
    )
  }

  const openCount =
    positions.length + dbPositions.filter((p) => p.status === 'OPEN' && !positions.find((ap) => ap.symbol === p.symbol)).length

  return (
    <div className="flex flex-col gap-4 pb-8">
      {llmAlert && (
        <div className="flex items-center gap-2 rounded-xl border border-signal-orange/30 bg-signal-orange/10 px-4 py-3 text-[13px] text-orange-300">
          <AlertTriangle size={16} />
          <span>Position monitor LLM unreachable — all positions on HOLD. {llmAlert}</span>
          <button className="ml-auto text-orange-300/70 hover:text-orange-200" onClick={onClearAlert}>✕</button>
        </div>
      )}
      {limits?.is_halted && (
        <div className="flex items-center gap-2 rounded-xl border border-signal-red/30 bg-signal-red/10 px-4 py-3 text-[13px] text-red-300">
          <AlertTriangle size={16} />
          <span>Daily loss limit reached — new BUY orders are paused for today.</span>
        </div>
      )}

      {/* Account stats */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-4">
        <AccountStat label="Portfolio Value" value={`$${portfolioVal.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`} />
        <AccountStat label="Equity" value={`$${equity.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`} />
        <AccountStat label="Buying Power" value={`$${buyingPower.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`} />
        <AccountStat label="Today's P&L" value={`${dayPnl >= 0 ? '+' : ''}$${dayPnl.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`} color={pnlCls(dayPnl)} />
        <AccountStat label="Trades Today" value={String(limits?.trade_count ?? 0)} />
        <AccountStat label="Realized P&L Today" value={`${(limits?.realized_pnl ?? 0) >= 0 ? '+' : ''}$${(limits?.realized_pnl ?? 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`} color={pnlCls(limits?.realized_pnl ?? 0)} />
        <AccountStat label="Total Earnings" value={`${allTimePnl >= 0 ? '+' : ''}$${allTimePnl.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`} color={pnlCls(allTimePnl)} />
      </div>

      <PerformanceHistory />
      <TodaysTrades positions={allPositions} alpacaPositions={positions} />

      <div className="flex items-center gap-2 text-[11px] text-ink-faint">
        <RefreshCw size={11} />
        {lastRefresh
          ? `Last synced ${lastRefresh.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`
          : 'Syncing…'}
        <button onClick={refresh} className="ml-2 rounded-md border border-line px-2 py-1 text-ink-muted hover:text-ink">
          Refresh
        </button>
      </div>

      {/* Open Positions */}
      <h3 className={sectionTitle}>
        Open Positions <span className={badge}>{openCount}</span>
      </h3>
      {openCount === 0 ? (
        <div className="rounded-xl border border-line bg-surface px-4 py-8 text-center text-sm text-ink-faint">No open positions</div>
      ) : (
        <div className="overflow-hidden rounded-xl border border-line bg-surface">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-line-soft">
                  {['Symbol', 'Side', 'Qty', 'Bought', 'Entry', 'Current', 'Mkt Value', 'Unrealized P&L', 'P&L %', 'Status', 'Sell'].map((c) => (
                    <th key={c} className={TH}>{c}</th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {positions.map((pos) => {
                  const plpc = parseFloat(pos.unrealized_plpc) * 100
                  const pl = parseFloat(pos.unrealized_pl)
                  const isLong = pos.side === 'long'
                  const entryTs = symbolEntryTime[pos.symbol]
                  return (
                    <tr key={pos.symbol} className="border-b border-line-faint hover:bg-surface-hover">
                      <td className={`${TD} font-mono font-bold`}>{pos.symbol}</td>
                      <td className={TD}><span className={dirChip(isLong)}>{isLong ? 'LONG' : 'SHORT'}</span></td>
                      <td className={`${TD} font-mono`}>{parseFloat(pos.qty).toLocaleString()}</td>
                      <td className={`${TD} text-ink-faint`}>{entryTs ? fmtDate(entryTs) : '—'}</td>
                      <td className={`${TD} font-mono`}>${parseFloat(pos.avg_entry_price).toFixed(2)}</td>
                      <td className={`${TD} font-mono`}>${parseFloat(pos.current_price).toFixed(2)}</td>
                      <td className={`${TD} font-mono`}>${parseFloat(pos.market_value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                      <td className={`${TD} font-mono ${pnlCls(pl)}`}>{pl >= 0 ? '+' : ''}${pl.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                      <td className={`${TD} font-mono ${pnlCls(pl)}`}>
                        <span className="inline-flex items-center gap-1">
                          {isLong ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
                          {plpc >= 0 ? '+' : ''}{plpc.toFixed(2)}%
                        </span>
                      </td>
                      <td className={TD}><span className={tag}>filled</span></td>
                      <td className={TD}>
                        <button
                          onClick={() => sellPosition(pos.symbol)}
                          disabled={!!selling}
                          title={`Close ${pos.symbol} at market`}
                          className="rounded-md border border-signal-red bg-signal-red/10 px-2.5 py-1 text-[11px] font-semibold text-red-300 hover:bg-signal-red hover:text-white disabled:opacity-50"
                        >
                          {selling === pos.symbol ? 'Closing…' : 'Sell'}
                        </button>
                      </td>
                    </tr>
                  )
                })}
                {dbPositions
                  .filter((p) => p.status === 'OPEN' && !positions.find((ap) => ap.symbol === p.symbol))
                  .map((p) => (
                    <tr key={p.id} className="border-b border-line-faint opacity-70">
                      <td className={`${TD} font-mono font-bold`}>{p.symbol}</td>
                      <td className={TD}><span className={dirChip(p.direction === 'LONG')}>{p.direction}</span></td>
                      <td className={`${TD} font-mono`}>{p.quantity.toLocaleString()}</td>
                      <td className={`${TD} text-ink-faint`}>{p.entry_time ? fmtDate(p.entry_time) : '—'}</td>
                      <td className={`${TD} text-ink-faint`}>{p.entry_price > 0 ? `$${p.entry_price.toFixed(2)}` : '—'}</td>
                      <td className={`${TD} text-ink-faint`}>—</td>
                      <td className={`${TD} text-ink-faint`}>—</td>
                      <td className={`${TD} text-ink-faint`}>—</td>
                      <td className={`${TD} text-ink-faint`}>—</td>
                      <td className={TD}><span className={`${tag} text-signal-yellow`}>pending fill</span></td>
                      <td className={`${TD} text-ink-faint`}>—</td>
                    </tr>
                  ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Performance Charts */}
      {(equityChartData.length > 0 || openBarData.length > 0 || cumulativeData.length > 0) && (
        <>
          <h3 className={`${sectionTitle} mt-4`}>Performance</h3>

          {equityChartData.length > 0 && (
            <ChartCard title="Portfolio Value Over Time">
              <ResponsiveContainer width="100%" height={260}>
                <LineChart data={equityChartData} margin={{ top: 8, right: 20, left: 0, bottom: 0 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                  <XAxis dataKey="date" tick={{ fontSize: 11, fill: '#94a3b8' }} />
                  <YAxis
                    tick={{ fontSize: 11, fill: '#94a3b8' }}
                    tickFormatter={(v) => `$${(v as number).toLocaleString('en-US', { maximumFractionDigits: 0 })}`}
                    width={80}
                    domain={['auto', 'auto']}
                  />
                  <Tooltip
                    content={({ active, payload, label }) => {
                      if (!active || !payload?.length) return null
                      const pt = payload[0]?.payload as { equity: number; isLive: boolean }
                      const val = pt.equity
                      const delta = val - baselineEquity
                      const deltaPct = baselineEquity ? (delta / baselineEquity) * 100 : 0
                      return (
                        <div className="rounded-lg border border-line-soft bg-surface-raised px-3.5 py-2.5">
                          <div className="text-[11px] font-semibold text-ink-muted">
                            {pt.isLive ? 'Now · live equity' : `${label} · market close`}
                          </div>
                          <div className="font-mono text-sm font-bold text-ink">${val.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</div>
                          <div className={`font-mono text-[11px] ${pnlCls(delta)}`}>
                            {delta >= 0 ? '+' : ''}${delta.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })} ({deltaPct >= 0 ? '+' : ''}{deltaPct.toFixed(2)}%) since start
                          </div>
                          {/* Position P&L is a live snapshot, so it only makes sense on the
                              live point — never stapled onto a historical close. */}
                          {pt.isLive && positions.length > 0 && (
                            <div className="mt-2 flex flex-col gap-1 border-t border-line-soft pt-1.5">
                              <div className="text-[10px] uppercase tracking-wide text-ink-faint">What you hold right now</div>
                              {positions.map((p) => {
                                const pl = parseFloat(p.unrealized_pl)
                                const mv = parseFloat(p.market_value)
                                return (
                                  <div key={p.symbol} className="flex items-center gap-2 text-[11px]">
                                    <span className="w-9 font-mono font-semibold">{p.symbol}</span>
                                    <span className="font-mono text-ink-faint">${mv.toLocaleString('en-US', { maximumFractionDigits: 0 })}</span>
                                    <span className={`ml-auto font-mono ${pnlCls(pl)}`}>{pl >= 0 ? '+' : ''}${pl.toFixed(2)}</span>
                                  </div>
                                )
                              })}
                              <div className="flex items-center gap-2 text-[11px] text-ink-faint">
                                <span className="w-9 font-mono">Cash</span>
                                <span className="font-mono">${cash.toLocaleString('en-US', { maximumFractionDigits: 0 })}</span>
                              </div>
                              <div className="mt-0.5 text-[10px] text-ink-faint">Holdings + cash = equity above</div>
                            </div>
                          )}
                        </div>
                      )
                    }}
                  />
                  <ReferenceLine y={baselineEquity} stroke="#475569" strokeDasharray="4 4" />
                  <Line
                    type="monotone"
                    dataKey="equity"
                    stroke="#3b82f6"
                    strokeWidth={2}
                    dot={(props: any) => {
                      const { cx, cy, payload, index } = props
                      if (!payload?.isLive) return <g key={index} />
                      return <circle key={index} cx={cx} cy={cy} r={5} fill="#3b82f6" stroke="#0d1117" strokeWidth={2} />
                    }}
                    activeDot={{ r: 5, fill: '#3b82f6' }}
                  />
                </LineChart>
              </ResponsiveContainer>
            </ChartCard>
          )}

          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            {openBarData.length > 0 && (
              <ChartCard title="Unrealized P&L (Open Positions)">
                <PnlBarChart data={openBarData} suffix="Unrealized P&L" />
              </ChartCard>
            )}
            {cumulativeData.length > 0 && (
              <ChartCard title="Cumulative Realized P&L">
                <ResponsiveContainer width="100%" height={220}>
                  <LineChart data={cumulativeData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                    <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                    <XAxis dataKey="date" tick={{ fontSize: 11, fill: '#94a3b8' }} />
                    <YAxis tick={{ fontSize: 11, fill: '#94a3b8' }} tickFormatter={(v) => `$${v}`} width={60} />
                    <Tooltip content={<PnlTooltip suffix="Cumulative P&L" />} />
                    <ReferenceLine y={0} stroke="#475569" />
                    <Line type="monotone" dataKey="cumPnl" stroke="#3b82f6" strokeWidth={2} dot={{ r: 3, fill: '#3b82f6' }} activeDot={{ r: 5 }} />
                  </LineChart>
                </ResponsiveContainer>
              </ChartCard>
            )}
            {tradeBarData.length > 0 && (
              <ChartCard title="P&L per Closed Trade">
                <PnlBarChart data={tradeBarData} suffix="Realized P&L" />
              </ChartCard>
            )}
          </div>
        </>
      )}

      {/* Closed Positions */}
      {dbPositions.filter((p) => p.status === 'CLOSED').length > 0 && (
        <>
          <h3 className={`${sectionTitle} mt-4`}>
            Closed Today <span className={badge}>{dbPositions.filter((p) => p.status === 'CLOSED').length}</span>
          </h3>
          <div className="overflow-hidden rounded-xl border border-line bg-surface">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-line-soft">
                    {['Symbol', 'Side', 'Bought', 'Entry', 'Exit', 'Realized P&L', 'Reason'].map((c) => (
                      <th key={c} className={TH}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {dbPositions.filter((p) => p.status === 'CLOSED').map((p) => {
                    const pl = p.realized_pnl ?? 0
                    return (
                      <tr key={p.id} className="border-b border-line-faint hover:bg-surface-hover">
                        <td className={`${TD} font-mono font-bold`}>{p.symbol}</td>
                        <td className={TD}><span className={dirChip(p.direction === 'LONG')}>{p.direction}</span></td>
                        <td className={`${TD} text-ink-faint`}>{p.entry_time ? fmtDate(p.entry_time) : '—'}</td>
                        <td className={`${TD} font-mono`}>${p.entry_price.toFixed(2)}</td>
                        <td className={`${TD} font-mono`}>{p.exit_price != null ? `$${p.exit_price.toFixed(2)}` : '—'}</td>
                        <td className={`${TD} font-mono ${pnlCls(pl)}`}>{pl >= 0 ? '+' : ''}${pl.toFixed(2)}</td>
                        <td className={TD}><span className={tag}>{p.close_reason || '—'}</span></td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {/* Failed Orders */}
      {failedOrders.length > 0 && (
        <>
          <h3 className={`${sectionTitle} mt-4 text-red-400`}>
            Failed Orders <span className="rounded-full bg-signal-red/15 px-2 py-0.5 text-[11px] font-semibold text-red-300">{failedOrders.length}</span>
          </h3>
          <div className="overflow-hidden rounded-xl border border-line bg-surface">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-line-soft">
                    {['Symbol', 'Direction', 'Qty', 'Strategy', 'Confidence', 'Failed At', ''].map((c, i) => (
                      <th key={i} className={TH}>{c}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {failedOrders.map((o) => (
                    <tr key={o.id} className="border-b border-line-faint hover:bg-surface-hover">
                      <td className={`${TD} font-mono font-bold`}>{o.symbol}</td>
                      <td className={TD}><span className={dirChip(o.direction === 'BUY')}>{o.direction}</span></td>
                      <td className={`${TD} font-mono`}>{o.quantity}</td>
                      <td className={`${TD} text-ink-faint`}>{o.strategy_name || '—'}</td>
                      <td className={`${TD} font-mono`}>{(o.confidence * 100).toFixed(0)}%</td>
                      <td className={`${TD} text-ink-faint`}>{fmtDate(o.updated_at)}</td>
                      <td className={TD}>
                        <button
                          onClick={() => retryOrder(o.id)}
                          disabled={!!retrying}
                          className="rounded-md border border-line bg-surface-hover px-2.5 py-1 text-[11px] font-semibold text-ink-muted hover:text-ink disabled:opacity-50"
                        >
                          {retrying === o.id ? 'Retrying…' : '↺ Retry'}
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}
    </div>
  )
}

function AccountStat({ label, value, color = '' }: { label: string; value: string; color?: string }) {
  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <div className={`tabular font-mono text-[19px] font-semibold ${color}`}>{value}</div>
      <div className="mt-1 text-[11px] uppercase tracking-wide text-ink-faint">{label}</div>
    </div>
  )
}

function ChartCard({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <div className="mb-3 text-[13px] font-semibold text-ink-muted">{title}</div>
      {children}
    </div>
  )
}

function PnlTooltip({ active, payload, label, suffix }: any) {
  if (!active || !payload?.length) return null
  const val = payload[0]?.value as number
  const color = val >= 0 ? '#22c55e' : '#ef4444'
  return (
    <div className="rounded-lg bg-surface-raised px-3 py-2" style={{ border: `1px solid ${color}` }}>
      <div className="text-[11px] text-ink-muted">{label}</div>
      <div className="text-sm font-bold" style={{ color }}>{val >= 0 ? '+' : ''}${val.toFixed(2)}</div>
      <div className="mt-0.5 text-[10px] text-ink-faint">{suffix}</div>
    </div>
  )
}

function PnlBarChart({ data, suffix }: { data: { name: string; pnl: number }[]; suffix: string }) {
  return (
    <ResponsiveContainer width="100%" height={220}>
      <BarChart data={data} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
        <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
        <XAxis dataKey="name" tick={{ fontSize: 11, fill: '#94a3b8' }} />
        <YAxis tick={{ fontSize: 11, fill: '#94a3b8' }} tickFormatter={(v) => `$${v}`} width={60} />
        <Tooltip content={<PnlTooltip suffix={suffix} />} />
        <ReferenceLine y={0} stroke="#475569" />
        <Bar dataKey="pnl" radius={[4, 4, 0, 0]}>
          {data.map((entry, i) => (
            <Cell key={i} fill={entry.pnl >= 0 ? '#22c55e' : '#ef4444'} />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}
