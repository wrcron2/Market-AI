import { useMemo } from 'react'
import { ArrowRight } from 'lucide-react'
import type { AlpacaAccount, AlpacaPosition, StagedOrder, TradingLimits } from '../types'
import type { Stats } from './PortfolioStats'
import type { FeedEvent } from './SignalFeed'
import { SignalFeed } from './SignalFeed'
import { BrainActivityFeed, type BrainEvent } from './BrainActivityFeed'
import { PageHead, SectionHead } from './ui/primitives'
import { fmtUSD, fmtSignedUSD, fmtPct, relTime, C } from '../lib/format'
import type { Tab } from './Dashboard'

interface Props {
  account: AlpacaAccount | null
  positions: AlpacaPosition[]
  limits: TradingLimits | null
  stats: Stats
  pendingOrders: StagedOrder[]
  feedEvents: FeedEvent[]
  brainEvents: BrainEvent[]
  marketOpen: boolean
  modeLabel: string
  onNavigate: (tab: Tab) => void
}

/**
 * Dashboard — template layout A ("Vitals"). Every number is live:
 * /api/alpaca/account, /api/alpaca/positions, /api/stats, /api/trading/limits,
 * plus the WS-fed signal/brain feeds.
 */
export function DashboardPage({
  account,
  positions,
  limits,
  stats,
  pendingOrders,
  feedEvents,
  brainEvents,
  marketOpen,
  modeLabel,
  onNavigate,
}: Props) {
  const portfolioValue = parseFloat(account?.portfolio_value ?? '0')
  const equity = parseFloat(account?.equity ?? '0')
  const lastEquity = parseFloat(account?.last_equity ?? '0')
  const cash = parseFloat(account?.cash ?? '0')
  const dayPnl = account ? equity - lastEquity : 0
  const dayPnlPct = lastEquity > 0 ? (dayPnl / lastEquity) * 100 : 0

  const gross = useMemo(
    () => positions.reduce((s, p) => s + Math.abs(parseFloat(p.market_value || '0')), 0),
    [positions],
  )
  const grossX = portfolioValue > 0 ? gross / portfolioValue : 0
  const unrealized = positions.reduce((s, p) => s + parseFloat(p.unrealized_pl || '0'), 0)

  const approvalRate =
    stats.totalSignals > 0 ? Math.round((stats.approved / stats.totalSignals) * 100) : null

  // Halt ladder: the only real deterministic threshold is the daily realized-loss
  // limit (DAILY_LOSS_LIMIT_USD, default $1,000 — backend/cmd/server/main.go).
  const dailyLossLimit = 1000
  const realizedToday = limits?.realized_pnl ?? 0
  const nowPct = Math.min(100, Math.max(0, (Math.abs(Math.min(0, realizedToday)) / dailyLossLimit) * 100))

  return (
    <div className="flex flex-col gap-[22px]">
      <PageHead
        eyebrow="Session"
        title={`${marketOpen ? 'Market watch' : 'After hours'} · ${modeLabel}`}
      />

      {/* Vitals */}
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-4">
        <Vital
          label="Net liquidation"
          value={account ? fmtUSD(portfolioValue, 0) : '—'}
          sub={account ? `cash ${fmtUSD(cash, 0)} · equity ${fmtUSD(equity, 0)}` : 'connecting to alpaca…'}
        />
        <Vital
          label="Day P&L"
          value={account ? fmtPct(dayPnlPct, 2) : '—'}
          valueColor={dayPnl >= 0 ? C.bright : C.danger}
          sub={account ? `${fmtSignedUSD(dayPnl)} · vs previous close equity` : '—'}
        />
        <Vital
          label="Gross exposure"
          value={account ? `${grossX.toFixed(2)}×` : '—'}
          sub={`${fmtUSD(gross, 0)} across ${positions.length} position${positions.length === 1 ? '' : 's'} · cash-only mode`}
          bar={Math.min(100, grossX * 100)}
        />
        <Vital
          label="Signals"
          value={String(stats.totalSignals)}
          sub={`${stats.approved} approved · ${stats.rejected} rejected · ${stats.executed} executed`}
        />
      </div>

      {/* Halt ladder */}
      <div className="mf-card flex flex-col gap-4 p-[22px]">
        <SectionHead eyebrow="Halt ladder" note="deterministic · fires without operator" />
        <div className="relative h-[52px]">
          <div className="absolute left-0 right-0 top-[24px] h-[3px] rounded-sm bg-line" />
          <div
            className="absolute left-0 top-[24px] h-[3px] rounded-sm bg-accent"
            style={{ width: `${nowPct}%` }}
          />
          <div className="absolute top-[6px] h-[40px] w-[2px] bg-ink" style={{ left: `${nowPct}%` }} />
          <div
            className="absolute top-[2px] text-[12px] font-medium leading-[1.4]"
            style={{ left: `calc(${nowPct}% + 10px)` }}
          >
            now {fmtSignedUSD(realizedToday, 0)}
          </div>
          <div className="absolute right-0 top-[8px] h-[36px] w-[2px] bg-signal-red" />
          <div className="absolute right-0 top-[34px] font-mono text-[11px] leading-[1.2] text-signal-red">
            −$1,000 day realized loss → halt new BUYs
          </div>
        </div>
        <div className="font-mono text-[11px] leading-[1.5] text-ink-faint">
          source · DAILY_LOSS_LIMIT_USD default, backend/cmd/server/main.go — the only halt threshold
          wired to live trading. /api/trading/limits exposes state, not thresholds. The four pre-set
          live-pilot kill criteria (docs/gates_kill_criteria.md §9) bind at gate G4 — see Roadmap.
          {limits?.is_halted && (
            <span className="text-signal-red"> HALTED TODAY — new BUY orders are paused.</span>
          )}
        </div>
      </div>

      {/* Real cards */}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        <div className="mf-card flex flex-col gap-3 p-[22px]">
          <div className="flex items-center gap-2">
            <span className="text-[15px] font-medium">Open positions</span>
            <span className="mf-tag-neutral">Alpaca paper</span>
            <span
              className="ml-auto font-mono text-[11px]"
              style={{ color: unrealized >= 0 ? C.bright : C.danger }}
            >
              {fmtSignedUSD(unrealized)}
            </span>
          </div>
          <div className="flex items-baseline gap-2.5">
            <span className="tabular text-[34px] font-medium leading-none tracking-[-0.03em]">
              {positions.length}
            </span>
            <span className="font-mono text-[13px] text-ink-muted">{fmtUSD(gross, 0)} gross</span>
          </div>
          <div className="mf-hairline" />
          <div className="flex flex-col gap-2 font-mono text-[13px] leading-[1.4]">
            {positions.length === 0 && <span className="text-ink-faint">flat — fully in cash</span>}
            {positions.slice(0, 5).map((p) => {
              const pl = parseFloat(p.unrealized_pl || '0')
              return (
                <div key={p.symbol} className="flex justify-between">
                  <span>
                    {p.symbol} · {parseFloat(p.qty)}
                  </span>
                  <span style={{ color: pl >= 0 ? C.bright : C.danger }}>{fmtSignedUSD(pl)}</span>
                </div>
              )
            })}
          </div>
          <span className="mt-auto font-mono text-[12px] leading-[1.5] text-ink-faint">
            full table + close buttons on Positions
          </span>
        </div>

        <div className="mf-card flex flex-col gap-3 p-[22px]">
          <div className="flex items-center gap-2">
            <span className="text-[15px] font-medium">Approval rate</span>
            <span className="mf-tag-neutral">this session's queue</span>
          </div>
          <span className="tabular text-[34px] font-medium leading-none tracking-[-0.03em]">
            {approvalRate != null ? `${approvalRate}%` : '—'}
          </span>
          <div className="mf-hairline" />
          <div className="font-mono text-[12px] leading-[1.6] text-ink-muted">
            {stats.totalSignals} staged · {stats.approved} approved · {stats.rejected} rejected
            <br />
            avg confidence{' '}
            {stats.avgConfidence > 0 ? `${Math.round(stats.avgConfidence * 100)}%` : '—'}
          </div>
          <span className="mt-auto font-mono text-[12px] leading-[1.5] text-ink-faint">
            source · /api/stats + live WebSocket events
          </span>
        </div>

        <div className="mf-card flex flex-col gap-3 p-[22px]">
          <div className="flex items-center gap-2">
            <span className="text-[15px] font-medium">Trades today</span>
            <span className="mf-tag-neutral">Alpaca</span>
            {limits?.is_halted && <span className="mf-tag-accent ml-auto">HALTED</span>}
          </div>
          <span className="tabular text-[34px] font-medium leading-none tracking-[-0.03em]">
            {limits?.trade_count ?? 0}
          </span>
          <div className="mf-hairline" />
          <div className="font-mono text-[12px] leading-[1.6] text-ink-muted">
            realized P&amp;L today{' '}
            <span style={{ color: realizedToday >= 0 ? C.bright : C.danger }}>
              {fmtSignedUSD(realizedToday)}
            </span>
          </div>
          <span className="mt-auto font-mono text-[12px] leading-[1.5] text-ink-faint">
            source · /api/trading/limits
          </span>
        </div>
      </div>

      {/* Waiting on you */}
      {pendingOrders.length > 0 && (
        <div className="mf-card-accent flex items-center gap-4 rounded-lg px-[22px] py-[17px]">
          <span className="mf-eyebrow">Waiting on you</span>
          <span className="text-[14px] leading-[1.4]">
            {pendingOrders.length} staged order{pendingOrders.length === 1 ? '' : 's'} awaiting a
            human · oldest {relTime(Math.min(...pendingOrders.map((o) => o.created_at)))}
          </span>
          <button onClick={() => onNavigate('green-light')} className="mf-btn-primary ml-auto">
            Open Green Light <ArrowRight size={14} />
          </button>
        </div>
      )}

      {/* Live feeds */}
      <div className="grid grid-cols-1 gap-4 xl:grid-cols-2">
        <SignalFeed events={feedEvents} />
        <BrainActivityFeed liveEvents={brainEvents} />
      </div>
    </div>
  )
}

function Vital({
  label,
  value,
  sub,
  valueColor,
  bar,
}: {
  label: string
  value: string
  sub: string
  valueColor?: string
  bar?: number
}) {
  return (
    <div className="mf-card flex flex-col gap-2.5 p-[22px]">
      <span className="mf-eyebrow">{label}</span>
      <span
        className="tabular text-[40px] font-medium leading-none tracking-[-0.03em]"
        style={valueColor ? { color: valueColor } : undefined}
      >
        {value}
      </span>
      {bar != null && (
        <div className="h-1 overflow-hidden rounded-sm bg-line">
          <div className="h-full bg-accent" style={{ width: `${bar}%` }} />
        </div>
      )}
      <span className="font-mono text-[12px] leading-[1.4] text-ink-muted">{sub}</span>
    </div>
  )
}
