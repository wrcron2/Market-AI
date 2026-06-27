import { Activity, CheckSquare, XSquare, Zap } from 'lucide-react'
import type { ReactNode } from 'react'

export interface Stats {
  totalSignals: number
  approved: number
  rejected: number
  executed: number
  avgConfidence: number
}

interface Props {
  stats: Stats
  wsConnected: boolean
}

/** PortfolioStats — session-level KPI cards. */
export function PortfolioStats({ stats, wsConnected }: Props) {
  const approvalRate =
    stats.totalSignals > 0 ? Math.round((stats.approved / stats.totalSignals) * 100) : 0

  const cards: { icon?: ReactNode; value: ReactNode; label: string; color?: string }[] = [
    { icon: <Activity size={18} />, value: stats.totalSignals, label: 'Total Signals', color: 'text-ink-faint' },
    { icon: <CheckSquare size={18} />, value: stats.approved, label: 'Approved', color: 'text-signal-green' },
    { icon: <XSquare size={18} />, value: stats.rejected, label: 'Rejected', color: 'text-signal-red' },
    { icon: <Zap size={18} />, value: stats.executed, label: 'Executed', color: 'text-signal-purple' },
    { value: stats.avgConfidence > 0 ? `${Math.round(stats.avgConfidence * 100)}%` : '—', label: 'Avg Confidence' },
    { value: `${approvalRate}%`, label: 'Approval Rate' },
  ]

  return (
    <div>
      <div className="mb-3 flex items-center gap-2 text-xs text-ink-faint">
        <span className={`h-2 w-2 rounded-full ${wsConnected ? 'bg-signal-green' : 'animate-pulse-dot bg-signal-orange'}`} />
        {wsConnected ? 'Live data' : 'Reconnecting…'}
      </div>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
        {cards.map((c, i) => (
          <div key={i} className="rounded-xl border border-line bg-surface p-4">
            {c.icon && <span className={`mb-2 inline-block ${c.color ?? 'text-ink-faint'}`}>{c.icon}</span>}
            <div className="tabular font-mono text-2xl font-semibold">{c.value}</div>
            <div className="mt-1 text-[11px] uppercase tracking-wide text-ink-faint">{c.label}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
