import { useState, useEffect } from 'react'
import {
  TrendingUp, TrendingDown, Target, BarChart3,
  ArrowUpRight, ArrowDownRight, Clock, Trophy, AlertTriangle,
} from 'lucide-react'
import {
  ResponsiveContainer, BarChart, Bar, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, ReferenceLine, Cell,
} from 'recharts'

interface StrategyReport {
  strategy_name: string
  total_trades: number
  winners: number
  losers: number
  win_rate: number
  total_pnl: number
  avg_pnl: number
  best_trade: number
  worst_trade: number
}

interface WeeklyProgress {
  week: string
  trades: number
  pnl: number
  cum_pnl: number
  winners: number
  losers: number
}

interface RecentTrade {
  symbol: string
  direction: string
  pnl: number
  pnl_pct: number
  exit_time: number
  reason: string
  hold_days: number
}

interface OutcomeStats {
  total_5d: number
  true_positive_5d: number
  false_positive_5d: number
  accuracy_5d: number
  avg_return_5d: number
  total_20d: number
  true_positive_20d: number
  accuracy_20d: number
  avg_return_20d: number
}

interface OrderStats {
  totalSignals: number
  approved: number
  rejected: number
  executed: number
  avgConfidence: number
}

interface ReportData {
  strategies: StrategyReport[] | null
  weekly: WeeklyProgress[] | null
  recent_trades: RecentTrade[] | null
  outcome_stats: OutcomeStats | null
  order_stats: OrderStats | null
  all_time_pnl: number
  total_closed: number
  total_wins: number
  overall_win_rate: number
  total_realized_pnl: number
}

function fmtUSD(n: number): string {
  return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

function fmtDate(ms: number): string {
  return new Date(ms).toLocaleDateString('en-US', { month: 'short', day: 'numeric' })
}

const pnlColor = (v: number) => (v >= 0 ? 'text-emerald-400' : 'text-red-400')
const pnlSign = (v: number) => (v >= 0 ? '+' : '')

export function ReportsPanel() {
  const [data, setData] = useState<ReportData | null>(null)
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    fetch('/api/reports/overview')
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d) setData(d) })
      .catch(() => {})
      .finally(() => setLoading(false))
  }, [])

  if (loading) {
    return (
      <div className="flex items-center gap-2.5 rounded-xl border border-line bg-surface px-4 py-6 text-sm text-ink-muted">
        <Clock size={18} className="animate-spin" />
        Loading reports...
      </div>
    )
  }

  if (!data) {
    return (
      <div className="flex items-center gap-2.5 rounded-xl border border-line bg-surface px-4 py-6 text-sm text-ink-faint">
        <AlertTriangle size={18} />
        Could not load report data.
      </div>
    )
  }

  const startingCapital = 100_000
  const totalReturn = data.all_time_pnl
  const totalReturnPct = (totalReturn / startingCapital) * 100

  const pendingSignals = (data.order_stats?.totalSignals ?? 0) -
    (data.order_stats?.approved ?? 0) -
    (data.order_stats?.rejected ?? 0)

  const weeklyData = (data.weekly ?? []).map(w => ({
    ...w,
    pnl: parseFloat(w.pnl.toFixed(2)),
    cum_pnl: parseFloat(w.cum_pnl.toFixed(2)),
  }))

  const recentTrades = data.recent_trades ?? []

  const winStreak = (() => {
    let streak = 0
    for (const t of recentTrades) {
      if (t.pnl > 0) streak++
      else break
    }
    return streak
  })()

  const lossStreak = (() => {
    let streak = 0
    for (const t of recentTrades) {
      if (t.pnl <= 0) streak++
      else break
    }
    return streak
  })()

  return (
    <div className="flex flex-col gap-5 pb-8">
      <div>
        <h1 className="text-[22px] font-semibold tracking-tight">Strategy Reports</h1>
        <p className="mt-1 text-[13px] text-ink-faint">
          Track trading progress, strategy performance, and signal accuracy.
        </p>
      </div>

      {/* Hero stats row */}
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
        <StatTile
          label="All-Time P&L"
          value={`${pnlSign(totalReturn)}$${fmtUSD(Math.abs(totalReturn))}`}
          color={pnlColor(totalReturn)}
          icon={totalReturn >= 0 ? TrendingUp : TrendingDown}
        />
        <StatTile
          label="Return"
          value={`${pnlSign(totalReturnPct)}${totalReturnPct.toFixed(2)}%`}
          color={pnlColor(totalReturnPct)}
          icon={totalReturnPct >= 0 ? ArrowUpRight : ArrowDownRight}
        />
        <StatTile
          label="Closed Trades"
          value={String(data.total_closed)}
          icon={BarChart3}
        />
        <StatTile
          label="Win Rate"
          value={`${data.overall_win_rate.toFixed(1)}%`}
          color={data.overall_win_rate >= 50 ? 'text-emerald-400' : 'text-red-400'}
          icon={Target}
        />
        <StatTile
          label={winStreak > 0 ? 'Win Streak' : 'Loss Streak'}
          value={String(winStreak > 0 ? winStreak : lossStreak)}
          color={winStreak > 0 ? 'text-emerald-400' : lossStreak > 0 ? 'text-red-400' : ''}
          icon={Trophy}
        />
      </div>

      {/* Signal pipeline summary */}
      {data.order_stats && (
        <div className="rounded-xl border border-line bg-surface p-4">
          <h3 className="mb-3 text-[13px] font-semibold text-ink-muted">Signal Pipeline</h3>
          <div className="grid grid-cols-2 gap-3 sm:grid-cols-5">
            <MiniStat label="Generated" value={data.order_stats.totalSignals} />
            <MiniStat label="Approved" value={data.order_stats.approved} color="text-emerald-400" />
            <MiniStat label="Rejected" value={data.order_stats.rejected} color="text-red-400" />
            <MiniStat label="Executed" value={data.order_stats.executed} color="text-blue-400" />
            <MiniStat label="Pending" value={pendingSignals} color={pendingSignals > 50 ? 'text-signal-yellow' : ''} />
          </div>
          {data.order_stats.avgConfidence > 0 && (
            <div className="mt-3 text-[11px] text-ink-faint">
              Avg confidence: {(data.order_stats.avgConfidence * 100).toFixed(1)}%
            </div>
          )}
        </div>
      )}

      {/* Weekly P&L chart */}
      {weeklyData.length > 0 && (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          <ChartCard title="Weekly P&L">
            <ResponsiveContainer width="100%" height={220}>
              <BarChart data={weeklyData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                <XAxis dataKey="week" tick={{ fontSize: 10, fill: '#94a3b8' }} />
                <YAxis tick={{ fontSize: 11, fill: '#94a3b8' }} tickFormatter={v => `$${v}`} width={60} />
                <Tooltip content={<PnlTooltip suffix="Weekly P&L" />} />
                <ReferenceLine y={0} stroke="#475569" />
                <Bar dataKey="pnl" radius={[4, 4, 0, 0]}>
                  {weeklyData.map((entry, i) => (
                    <Cell key={i} fill={entry.pnl >= 0 ? '#22c55e' : '#ef4444'} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
          </ChartCard>

          <ChartCard title="Cumulative P&L Progress">
            <ResponsiveContainer width="100%" height={220}>
              <LineChart data={weeklyData} margin={{ top: 8, right: 16, left: 0, bottom: 0 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.06)" />
                <XAxis dataKey="week" tick={{ fontSize: 10, fill: '#94a3b8' }} />
                <YAxis tick={{ fontSize: 11, fill: '#94a3b8' }} tickFormatter={v => `$${v}`} width={60} />
                <Tooltip content={<PnlTooltip suffix="Cumulative" />} />
                <ReferenceLine y={0} stroke="#475569" />
                <Line type="monotone" dataKey="cum_pnl" stroke="#3b82f6" strokeWidth={2} dot={{ r: 3, fill: '#3b82f6' }} activeDot={{ r: 5 }} />
              </LineChart>
            </ResponsiveContainer>
          </ChartCard>
        </div>
      )}

      {/* Strategy breakdown */}
      {(data.strategies ?? []).length > 0 && (
        <>
          <h3 className="flex items-center gap-2.5 text-base font-semibold">
            <Target size={16} />
            Strategy Breakdown
          </h3>
          <div className="grid grid-cols-1 gap-3 lg:grid-cols-2">
            {(data.strategies ?? []).map(s => (
              <StrategyCard key={s.strategy_name} strategy={s} />
            ))}
          </div>
        </>
      )}

      {/* Signal accuracy */}
      {data.outcome_stats && (data.outcome_stats.total_5d > 0 || data.outcome_stats.total_20d > 0) && (
        <div className="rounded-xl border border-line bg-surface p-4">
          <h3 className="mb-3 flex items-center gap-2.5 text-[13px] font-semibold text-ink-muted">
            <Target size={14} />
            Signal Prediction Accuracy
          </h3>
          <div className="grid grid-cols-2 gap-4">
            <AccuracyBlock
              label="5-Day Accuracy"
              total={data.outcome_stats.total_5d}
              hits={data.outcome_stats.true_positive_5d}
              accuracy={data.outcome_stats.accuracy_5d}
              avgReturn={data.outcome_stats.avg_return_5d}
            />
            <AccuracyBlock
              label="20-Day Accuracy"
              total={data.outcome_stats.total_20d}
              hits={data.outcome_stats.true_positive_20d}
              accuracy={data.outcome_stats.accuracy_20d}
              avgReturn={data.outcome_stats.avg_return_20d}
            />
          </div>
        </div>
      )}

      {/* Recent closed trades */}
      {recentTrades.length > 0 && (
        <>
          <h3 className="flex items-center gap-2.5 text-base font-semibold">
            <Clock size={16} />
            Recent Closed Trades
            <span className="rounded-full bg-surface-sunken px-2 py-0.5 text-[11px] font-semibold text-ink-muted">
              {recentTrades.length}
            </span>
          </h3>
          <div className="overflow-hidden rounded-xl border border-line bg-surface">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-line-soft">
                    {['Symbol', 'Side', 'P&L', 'Return', 'Hold', 'Reason', 'Closed'].map(c => (
                      <th key={c} className="whitespace-nowrap px-4 py-2.5 text-left text-[10.5px] font-semibold uppercase tracking-wide text-ink-faint">
                        {c}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {recentTrades.map((t, i) => {
                    const isLong = t.direction === 'LONG'
                    return (
                      <tr key={i} className="border-b border-line-faint hover:bg-surface-hover">
                        <td className="whitespace-nowrap px-4 py-3 font-mono text-[13px] font-bold">{t.symbol}</td>
                        <td className="whitespace-nowrap px-4 py-3 text-[13px]">
                          <span className={`rounded px-1.5 py-0.5 text-[10px] font-semibold ${
                            isLong ? 'bg-emerald-500/15 text-emerald-400' : 'bg-red-500/15 text-red-400'
                          }`}>
                            {t.direction}
                          </span>
                        </td>
                        <td className={`whitespace-nowrap px-4 py-3 font-mono text-[13px] ${pnlColor(t.pnl)}`}>
                          {pnlSign(t.pnl)}${fmtUSD(Math.abs(t.pnl))}
                        </td>
                        <td className={`whitespace-nowrap px-4 py-3 font-mono text-[13px] ${pnlColor(t.pnl_pct)}`}>
                          {pnlSign(t.pnl_pct)}{t.pnl_pct.toFixed(2)}%
                        </td>
                        <td className="whitespace-nowrap px-4 py-3 text-[13px] text-ink-faint">
                          {t.hold_days < 1 ? '<1d' : `${t.hold_days.toFixed(0)}d`}
                        </td>
                        <td className="whitespace-nowrap px-4 py-3 text-[13px]">
                          <span className="rounded bg-surface-sunken px-2 py-0.5 text-[10px] font-semibold text-ink-muted">
                            {t.reason || '--'}
                          </span>
                        </td>
                        <td className="whitespace-nowrap px-4 py-3 text-[13px] text-ink-faint">
                          {t.exit_time > 0 ? fmtDate(t.exit_time) : '--'}
                        </td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          </div>
        </>
      )}

      {/* Empty state */}
      {data.total_closed === 0 && (
        <div className="rounded-xl border border-line bg-surface px-6 py-12 text-center">
          <BarChart3 size={32} className="mx-auto mb-3 text-ink-faint" />
          <div className="text-sm font-semibold text-ink-muted">No closed trades yet</div>
          <div className="mt-1 text-[12px] text-ink-faint">
            Strategy reports will appear here once positions are closed.
          </div>
        </div>
      )}
    </div>
  )
}

function StatTile({ label, value, color = '', icon: Icon }: {
  label: string; value: string; color?: string; icon: React.ComponentType<{ size?: number }>
}) {
  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <div className="flex items-center gap-2 text-[11px] uppercase tracking-wide text-ink-faint">
        <Icon size={13} />
        {label}
      </div>
      <div className={`mt-1.5 font-mono text-[19px] font-semibold ${color}`}>{value}</div>
    </div>
  )
}

function MiniStat({ label, value, color = '' }: { label: string; value: number; color?: string }) {
  return (
    <div>
      <div className={`font-mono text-[17px] font-semibold ${color}`}>{value.toLocaleString()}</div>
      <div className="text-[10px] uppercase tracking-wide text-ink-faint">{label}</div>
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

function StrategyCard({ strategy: s }: { strategy: StrategyReport }) {
  const barWidth = s.total_trades > 0 ? (s.winners / s.total_trades) * 100 : 0
  return (
    <div className="rounded-xl border border-line bg-surface p-4">
      <div className="flex items-center justify-between">
        <div className="font-mono text-sm font-bold">{s.strategy_name || 'Unknown'}</div>
        <div className={`font-mono text-sm font-semibold ${pnlColor(s.total_pnl)}`}>
          {pnlSign(s.total_pnl)}${fmtUSD(Math.abs(s.total_pnl))}
        </div>
      </div>

      {/* Win rate bar */}
      <div className="mt-3">
        <div className="mb-1 flex justify-between text-[10px] text-ink-faint">
          <span>{s.winners}W / {s.losers}L ({s.total_trades} trades)</span>
          <span className={s.win_rate >= 50 ? 'text-emerald-400' : 'text-red-400'}>
            {s.win_rate.toFixed(1)}% win rate
          </span>
        </div>
        <div className="flex h-2 overflow-hidden rounded-full bg-red-500/20">
          <div
            className="rounded-full bg-emerald-500 transition-all"
            style={{ width: `${barWidth}%` }}
          />
        </div>
      </div>

      <div className="mt-3 grid grid-cols-3 gap-2 text-center text-[11px]">
        <div>
          <div className="font-mono font-semibold">${fmtUSD(Math.abs(s.avg_pnl))}</div>
          <div className="text-ink-faint">Avg P&L</div>
        </div>
        <div>
          <div className="font-mono font-semibold text-emerald-400">${fmtUSD(s.best_trade)}</div>
          <div className="text-ink-faint">Best</div>
        </div>
        <div>
          <div className="font-mono font-semibold text-red-400">-${fmtUSD(Math.abs(s.worst_trade))}</div>
          <div className="text-ink-faint">Worst</div>
        </div>
      </div>
    </div>
  )
}

function AccuracyBlock({ label, total, hits, accuracy, avgReturn }: {
  label: string; total: number; hits: number; accuracy: number; avgReturn: number
}) {
  if (total === 0) return null
  const barWidth = accuracy
  return (
    <div>
      <div className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">{label}</div>
      <div className="mt-2 flex items-end gap-2">
        <span className={`font-mono text-[22px] font-bold ${accuracy >= 50 ? 'text-emerald-400' : 'text-red-400'}`}>
          {accuracy.toFixed(1)}%
        </span>
        <span className="pb-1 text-[11px] text-ink-faint">({hits}/{total} correct)</span>
      </div>
      <div className="mt-1.5 flex h-1.5 overflow-hidden rounded-full bg-surface-sunken">
        <div
          className={`rounded-full transition-all ${accuracy >= 50 ? 'bg-emerald-500' : 'bg-red-500'}`}
          style={{ width: `${barWidth}%` }}
        />
      </div>
      <div className={`mt-1 font-mono text-[11px] ${pnlColor(avgReturn)}`}>
        Avg return: {pnlSign(avgReturn)}{(avgReturn * 100).toFixed(2)}%
      </div>
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
      <div className="text-sm font-bold" style={{ color }}>{pnlSign(val)}${fmtUSD(Math.abs(val))}</div>
      <div className="mt-0.5 text-[10px] text-ink-faint">{suffix}</div>
    </div>
  )
}
