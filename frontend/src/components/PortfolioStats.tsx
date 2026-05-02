import { Activity, CheckSquare, XSquare, Zap } from 'lucide-react'

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

/**
 * PortfolioStats — top-of-page KPI cards showing session-level metrics.
 */
export function PortfolioStats({ stats, wsConnected }: Props) {
  const approvalRate = stats.totalSignals > 0
    ? Math.round((stats.approved / stats.totalSignals) * 100)
    : 0

  return (
    <div className="portfolio-stats">
      <div className="stat-card">
        <Activity size={20} />
        <div className="stat-value">{stats.totalSignals}</div>
        <div className="stat-label">Total Signals</div>
      </div>

      <div className="stat-card approved">
        <CheckSquare size={20} />
        <div className="stat-value">{stats.approved}</div>
        <div className="stat-label">Approved</div>
      </div>

      <div className="stat-card rejected">
        <XSquare size={20} />
        <div className="stat-value">{stats.rejected}</div>
        <div className="stat-label">Rejected</div>
      </div>

      <div className="stat-card executed">
        <Zap size={20} />
        <div className="stat-value">{stats.executed}</div>
        <div className="stat-label">Executed</div>
      </div>

      <div className="stat-card confidence">
        <div className="stat-value">
          {stats.avgConfidence > 0 ? `${Math.round(stats.avgConfidence * 100)}%` : '—'}
        </div>
        <div className="stat-label">Avg Confidence</div>
      </div>

      <div className="stat-card approval-rate">
        <div className="stat-value">{approvalRate}%</div>
        <div className="stat-label">Approval Rate</div>
      </div>

      {/* Live connection indicator */}
      <div className={`ws-indicator ${wsConnected ? 'connected' : 'disconnected'}`}>
        <span className="ws-dot" />
        {wsConnected ? 'Live' : 'Reconnecting…'}
      </div>
    </div>
  )
}
