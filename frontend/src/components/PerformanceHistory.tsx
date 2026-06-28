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
    <div>
      <h3 className="mb-3 flex items-center gap-2.5 text-base font-semibold">Portfolio Performance</h3>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 lg:grid-cols-5">
        {periods.map((p) => {
          const positive = p.pnl >= 0
          const Icon = positive ? TrendingUp : TrendingDown
          return (
            <div
              key={p.period}
              className={`rounded-xl border p-4 ${
                positive
                  ? 'border-emerald-500/20 bg-emerald-500/5'
                  : 'border-red-500/20 bg-red-500/5'
              }`}
            >
              <div className="text-[11px] font-semibold uppercase tracking-wide text-ink-faint">{p.label}</div>
              <div className={`mt-1.5 flex items-center gap-1.5 font-mono text-[17px] font-bold ${positive ? 'text-emerald-400' : 'text-red-400'}`}>
                <Icon size={14} />
                {positive ? '+' : ''}${fmt$(p.pnl)}
              </div>
              <div className={`mt-0.5 font-mono text-[13px] font-semibold ${positive ? 'text-emerald-400' : 'text-red-400'}`}>
                {positive ? '+' : ''}{p.pnl_pct.toFixed(2)}%
              </div>
              <div className="mt-1.5 text-[11px] text-ink-faint">
                ${fmt$(p.start_value)} → ${fmt$(p.end_value)}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
