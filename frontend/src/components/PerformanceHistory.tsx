import { useState, useEffect } from 'react'
import { TrendingUp, TrendingDown } from 'lucide-react'

interface PeriodResult {
  label: string
  period: string
  start_value: number
  end_value: number
  pnl: number
  pnl_pct: number
}

function fmt$(n: number): string {
  return n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
}

export function PerformanceHistory() {
  const [periods, setPeriods] = useState<PeriodResult[]>([])

  useEffect(() => {
    fetch('/api/alpaca/portfolio-history')
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (d?.periods) setPeriods(d.periods) })
      .catch(() => {})
  }, [])

  if (periods.length === 0) return null

  return (
    <div className="perf-history">
      <h3 className="alpaca-section-title">Portfolio Performance</h3>
      <div className="perf-history-row">
        {periods.map((p) => {
          const positive = p.pnl >= 0
          const Icon = positive ? TrendingUp : TrendingDown
          return (
            <div key={p.period} className={`perf-card ${positive ? 'perf-card-up' : 'perf-card-down'}`}>
              <div className="perf-card-label">{p.label}</div>
              <div className="perf-card-pnl">
                <Icon size={14} />
                {positive ? '+' : ''}${fmt$(p.pnl)}
              </div>
              <div className={`perf-card-pct ${positive ? 'pnl-positive' : 'pnl-negative'}`}>
                {positive ? '+' : ''}{p.pnl_pct.toFixed(2)}%
              </div>
              <div className="perf-card-range">
                ${fmt$(p.start_value)} → ${fmt$(p.end_value)}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
