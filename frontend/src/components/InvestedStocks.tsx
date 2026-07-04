import { useState, useEffect, useCallback } from 'react'
import { Briefcase, TrendingUp, TrendingDown } from 'lucide-react'

interface AlpacaPosition {
  symbol: string
  qty: string
  side: string
  avg_entry_price: string
  current_price: string
  market_value: string
  cost_basis: string
  unrealized_pl: string
  unrealized_plpc: string
  change_today: string
}

interface AccountSummary {
  portfolio_value: string
  cash: string
  buying_power: string
}

const fmtUsd = (n: number) =>
  (n < 0 ? '-' : '') + '$' + Math.abs(n).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })

// Shows what the account actually holds right now — the direct answer to
// "is anything invested?". Data comes straight from Alpaca.
export function InvestedStocks() {
  const [positions, setPositions] = useState<AlpacaPosition[]>([])
  const [account, setAccount] = useState<AccountSummary | null>(null)
  const [loading, setLoading] = useState(true)

  const load = useCallback(async () => {
    try {
      const [posRes, accRes] = await Promise.all([
        fetch('/api/alpaca/positions'),
        fetch('/api/alpaca/account'),
      ])
      if (posRes.ok) setPositions(await posRes.json())
      if (accRes.ok) setAccount(await accRes.json())
    } catch { /* keep last data */ }
    finally { setLoading(false) }
  }, [])

  useEffect(() => {
    load()
    const iv = setInterval(load, 30_000)
    return () => clearInterval(iv)
  }, [load])

  const totalPL = positions.reduce((s, p) => s + parseFloat(p.unrealized_pl || '0'), 0)
  const totalValue = positions.reduce((s, p) => s + parseFloat(p.market_value || '0'), 0)

  return (
    <div style={{ background: '#131720', border: '1px solid #1e293b', borderRadius: 14, padding: '18px 20px' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 12, flexWrap: 'wrap', gap: 8 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <Briefcase size={16} style={{ color: '#3b82f6' }} />
          <span style={{ fontSize: 14, fontWeight: 700, color: '#e2e8f0' }}>
            Invested Stocks
          </span>
          <span style={{ background: '#1e293b', color: '#94a3b8', borderRadius: 10, padding: '2px 8px', fontSize: 11, fontWeight: 600 }}>
            {positions.length} open position{positions.length === 1 ? '' : 's'}
          </span>
        </div>
        <div style={{ display: 'flex', gap: 14, fontSize: 12, flexWrap: 'wrap' }}>
          <span style={{ color: '#64748b' }}>
            Invested: <b style={{ color: '#e2e8f0' }}>{fmtUsd(totalValue)}</b>
          </span>
          <span style={{ color: '#64748b' }}>
            Unrealized P&L:{' '}
            <b style={{ color: totalPL >= 0 ? '#22c55e' : '#ef4444' }}>
              {totalPL >= 0 ? '+' : ''}{fmtUsd(totalPL)}
            </b>
          </span>
          {account && (
            <span style={{ color: '#64748b' }}>
              Cash: <b style={{ color: parseFloat(account.cash) < 0 ? '#f59e0b' : '#e2e8f0' }}>{fmtUsd(parseFloat(account.cash))}</b>
              {parseFloat(account.cash) < 0 && (
                <span style={{ color: '#f59e0b', marginLeft: 4 }} title="Negative cash = positions bought on margin">
                  (margin)
                </span>
              )}
            </span>
          )}
        </div>
      </div>

      {loading && <p style={{ color: '#64748b', fontSize: 13, margin: 0 }}>Loading positions…</p>}
      {!loading && positions.length === 0 && (
        <p style={{ color: '#64748b', fontSize: 13, margin: 0 }}>
          No open positions — the account is fully in cash.
        </p>
      )}

      {positions.length > 0 && (
        <div style={{ overflowX: 'auto' }}>
          <table style={{ width: '100%', borderCollapse: 'collapse', fontSize: 12.5, color: '#cbd5e1' }}>
            <thead>
              <tr style={{ borderBottom: '1px solid #1e293b', color: '#64748b', textAlign: 'left' }}>
                {['Symbol', 'Side', 'Qty', 'Entry', 'Current', 'Market Value', 'Unrealized P&L', 'Today'].map(h => (
                  <th key={h} style={{ padding: '6px 10px', fontWeight: 600, whiteSpace: 'nowrap' }}>{h}</th>
                ))}
              </tr>
            </thead>
            <tbody>
              {positions.map((p) => {
                const pl = parseFloat(p.unrealized_pl || '0')
                const plPct = parseFloat(p.unrealized_plpc || '0') * 100
                const today = parseFloat(p.change_today || '0') * 100
                const plColor = pl >= 0 ? '#22c55e' : '#ef4444'
                return (
                  <tr key={p.symbol} style={{ borderBottom: '1px solid #0f172a' }}>
                    <td style={{ padding: '7px 10px', fontWeight: 700, color: '#93c5fd' }}>{p.symbol}</td>
                    <td style={{ padding: '7px 10px', textTransform: 'uppercase', color: '#94a3b8', fontSize: 11 }}>{p.side}</td>
                    <td style={{ padding: '7px 10px' }}>{parseFloat(p.qty).toLocaleString()}</td>
                    <td style={{ padding: '7px 10px' }}>{fmtUsd(parseFloat(p.avg_entry_price))}</td>
                    <td style={{ padding: '7px 10px' }}>{fmtUsd(parseFloat(p.current_price))}</td>
                    <td style={{ padding: '7px 10px' }}>{fmtUsd(parseFloat(p.market_value))}</td>
                    <td style={{ padding: '7px 10px', color: plColor, fontWeight: 600, whiteSpace: 'nowrap' }}>
                      <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4 }}>
                        {pl >= 0 ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
                        {pl >= 0 ? '+' : ''}{fmtUsd(pl)} ({plPct >= 0 ? '+' : ''}{plPct.toFixed(2)}%)
                      </span>
                    </td>
                    <td style={{ padding: '7px 10px', color: today >= 0 ? '#22c55e' : '#ef4444', fontSize: 12 }}>
                      {today >= 0 ? '+' : ''}{today.toFixed(2)}%
                    </td>
                  </tr>
                )
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  )
}
