import { useState, useEffect, useCallback } from 'react'
import { TrendingUp, TrendingDown, RefreshCw, AlertTriangle } from 'lucide-react'
import type { AlpacaAccount, AlpacaPosition, TradingLimits, Position } from '../types'

const REFRESH_MS = 30_000

interface Props {
  llmAlert: string | null
  onClearAlert: () => void
}

export function AlpacaPortfolio({ llmAlert, onClearAlert }: Props) {
  const [account,    setAccount]    = useState<AlpacaAccount | null>(null)
  const [positions,  setPositions]  = useState<AlpacaPosition[]>([])
  const [dbPositions, setDbPositions] = useState<Position[]>([])
  const [limits,     setLimits]     = useState<TradingLimits | null>(null)
  const [loading,    setLoading]    = useState(true)
  const [error,      setError]      = useState<string | null>(null)
  const [lastRefresh, setLastRefresh] = useState<Date | null>(null)

  const refresh = useCallback(async () => {
    try {
      const [acctRes, posRes, dbPosRes, limRes] = await Promise.all([
        fetch('/api/alpaca/account'),
        fetch('/api/alpaca/positions'),
        fetch('/api/positions'),
        fetch('/api/trading/limits'),
      ])

      if (acctRes.ok) setAccount(await acctRes.json())
      if (posRes.ok)  setPositions(await posRes.json())
      if (dbPosRes.ok) {
        const data = await dbPosRes.json()
        setDbPositions(data.positions ?? [])
      }
      if (limRes.ok)  setLimits(await limRes.json())
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

  const equity       = parseFloat(account?.equity ?? '0')
  const lastEquity   = parseFloat(account?.last_equity ?? '0')
  const dayPnl       = equity - lastEquity
  const buyingPower  = parseFloat(account?.buying_power ?? '0')
  const portfolioVal = parseFloat(account?.portfolio_value ?? '0')

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
      </div>
    )
  }

  return (
    <div className="alpaca-portfolio">
      {/* LLM unreachable alert */}
      {llmAlert && (
        <div className="llm-alert">
          <AlertTriangle size={16} />
          <span>Position monitor LLM unreachable — all positions on HOLD. {llmAlert}</span>
          <button className="llm-alert-close" onClick={onClearAlert}>✕</button>
        </div>
      )}

      {/* Daily halt warning */}
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
        <AccountStat
          label="Today's P&L"
          value={`${dayPnl >= 0 ? '+' : ''}$${dayPnl.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
          className={dayPnl >= 0 ? 'pnl-positive' : 'pnl-negative'}
        />
        <AccountStat label="Trades Today" value={String(limits?.trade_count ?? 0)} />
        <AccountStat
          label="Realized P&L"
          value={`${(limits?.realized_pnl ?? 0) >= 0 ? '+' : ''}$${(limits?.realized_pnl ?? 0).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`}
          className={(limits?.realized_pnl ?? 0) >= 0 ? 'pnl-positive' : 'pnl-negative'}
        />
      </div>

      {/* Last refresh */}
      <div className="alpaca-refresh-row">
        <RefreshCw size={11} />
        {lastRefresh
          ? `Last synced ${lastRefresh.toLocaleTimeString('en-US', { hour: '2-digit', minute: '2-digit', second: '2-digit' })}`
          : 'Syncing…'}
        <button className="refresh-btn" onClick={refresh}>Refresh</button>
      </div>

      {/* Open positions table */}
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
                <th>Entry</th>
                <th>Current</th>
                <th>Mkt Value</th>
                <th>Unrealized P&L</th>
                <th>P&L %</th>
                <th>Status</th>
              </tr>
            </thead>
            <tbody>
              {/* Filled positions from Alpaca live feed */}
              {positions.map((pos) => {
                const plpc    = parseFloat(pos.unrealized_plpc) * 100
                const pl      = parseFloat(pos.unrealized_pl)
                const isLong  = pos.side === 'long'
                const plColor = pl >= 0 ? 'pnl-positive' : 'pnl-negative'
                return (
                  <tr key={pos.symbol}>
                    <td className="pos-symbol">{pos.symbol}</td>
                    <td><span className={`direction-badge ${isLong ? 'buy' : 'sell'}`}>{isLong ? 'LONG' : 'SHORT'}</span></td>
                    <td>{parseFloat(pos.qty).toLocaleString()}</td>
                    <td>${parseFloat(pos.avg_entry_price).toFixed(2)}</td>
                    <td>${parseFloat(pos.current_price).toFixed(2)}</td>
                    <td>${parseFloat(pos.market_value).toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}</td>
                    <td className={plColor}>
                      {pl >= 0 ? '+' : ''}${pl.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}
                    </td>
                    <td className={plColor}>
                      <span className="plpc-cell">
                        {isLong ? <TrendingUp size={12} /> : <TrendingDown size={12} />}
                        {plpc >= 0 ? '+' : ''}{plpc.toFixed(2)}%
                      </span>
                    </td>
                    <td><span className="close-reason-tag">filled</span></td>
                  </tr>
                )
              })}
              {/* Pending positions: in our DB but not yet filled by Alpaca (market closed) */}
              {dbPositions
                .filter(p => p.status === 'OPEN' && !positions.find(ap => ap.symbol === p.symbol))
                .map((p) => (
                  <tr key={p.id} className="pending-row">
                    <td className="pos-symbol">{p.symbol}</td>
                    <td><span className={`direction-badge ${p.direction === 'LONG' ? 'buy' : 'sell'}`}>{p.direction}</span></td>
                    <td>{p.quantity.toLocaleString()}</td>
                    <td className="text-muted">{p.entry_price > 0 ? `$${p.entry_price.toFixed(2)}` : '—'}</td>
                    <td className="text-muted">—</td>
                    <td className="text-muted">—</td>
                    <td className="text-muted">—</td>
                    <td className="text-muted">—</td>
                    <td><span className="close-reason-tag pending-tag">⏳ pending fill</span></td>
                  </tr>
                ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Recently closed positions */}
      {dbPositions.filter((p) => p.status === 'CLOSED').length > 0 && (
        <>
          <h3 className="alpaca-section-title" style={{ marginTop: '24px' }}>
            Closed Today
            <span className="badge">{dbPositions.filter((p) => p.status === 'CLOSED').length}</span>
          </h3>
          <div className="positions-table-wrap">
            <table className="positions-table">
              <thead>
                <tr>
                  <th>Symbol</th>
                  <th>Side</th>
                  <th>Entry</th>
                  <th>Exit</th>
                  <th>Realized P&L</th>
                  <th>Reason</th>
                </tr>
              </thead>
              <tbody>
                {dbPositions
                  .filter((p) => p.status === 'CLOSED')
                  .map((p) => {
                    const pl = p.realized_pnl ?? 0
                    return (
                      <tr key={p.id} className="closed-row">
                        <td className="pos-symbol">{p.symbol}</td>
                        <td>
                          <span className={`direction-badge ${p.direction === 'LONG' ? 'buy' : 'sell'}`}>
                            {p.direction}
                          </span>
                        </td>
                        <td>${p.entry_price.toFixed(2)}</td>
                        <td>{p.exit_price != null ? `$${p.exit_price.toFixed(2)}` : '—'}</td>
                        <td className={pl >= 0 ? 'pnl-positive' : 'pnl-negative'}>
                          {pl >= 0 ? '+' : ''}${pl.toFixed(2)}
                        </td>
                        <td><span className="close-reason-tag">{p.close_reason || '—'}</span></td>
                      </tr>
                    )
                  })}
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
