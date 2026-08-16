import { useEffect, useState } from 'react'
import { Bell, PanelRightClose, Zap, Pause, AlertTriangle } from 'lucide-react'
import { StatusDot } from '../ui/primitives'
import { fmtUSD, fmtSignedUSD, C } from '../../lib/format'
import type { TradingMode } from '../TradingModeToggle'

interface Props {
  wsConnected: boolean
  autoExec: boolean
  mode: TradingMode
  portfolioValue: number | null
  dayPnl: number | null
  marketOpen: boolean
  marketMinutes: number | null
  alertCount?: number
  llmDegraded?: boolean
  onToggleAsk: () => void
  onBell: () => void
  onKill: () => void
}

const fmtTz = (d: Date, tz: string) =>
  d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: tz })

export function TopBar({
  wsConnected,
  autoExec,
  mode,
  portfolioValue,
  dayPnl,
  marketOpen,
  marketMinutes,
  alertCount = 0,
  llmDegraded = false,
  onToggleAsk,
  onBell,
  onKill,
}: Props) {
  const [now, setNow] = useState(() => new Date())
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1_000)
    return () => clearInterval(id)
  }, [])

  const isSim = mode === 'yahoo'
  const modeLabel = isSim ? 'Sim · Yahoo' : 'Paper · Alpaca'
  const modeColor = isSim ? C.warn : C.bright

  return (
    <header className="sticky top-0 z-30 flex shrink-0 items-center gap-[22px] border-b border-ink/10 bg-base px-7 py-3.5">
      {/* Mode */}
      <div className="flex items-center gap-2">
        <span
          className="h-[7px] w-[7px] animate-pulse-dot rounded-full"
          style={{ background: modeColor }}
        />
        <span
          className="whitespace-nowrap text-[12px] font-medium uppercase"
          style={{ letterSpacing: '.06em', color: modeColor }}
        >
          {modeLabel}
        </span>
      </div>

      {/* Dual clock */}
      <div className="hidden whitespace-nowrap font-mono text-[12px] text-ink-muted md:block">
        {fmtTz(now, 'Asia/Jerusalem')} IL · {fmtTz(now, 'America/New_York')} ET
        {' · '}
        {marketOpen
          ? `market open${marketMinutes != null ? `, ${marketMinutes} min to close` : ''}`
          : 'market closed'}
      </div>

      {!wsConnected && (
        <span className="hidden items-center gap-1.5 font-mono text-[11px] text-signal-orange lg:flex">
          <StatusDot color={C.warn} pulse size={7} /> reconnecting…
        </span>
      )}
      {llmDegraded && (
        <span className="hidden items-center gap-1.5 font-mono text-[11px] text-signal-orange lg:flex">
          <AlertTriangle size={12} /> llm degraded — ollama fallback
        </span>
      )}
      <span
        className="hidden items-center gap-1.5 font-mono text-[11px] text-ink-faint xl:flex"
        title="Auto-execute state (change on the Ops page)"
      >
        {autoExec ? <Zap size={12} className="text-signal-yellow" /> : <Pause size={12} />}
        {autoExec ? 'auto on' : 'auto off'}
      </span>

      {/* Right */}
      <div className="ml-auto flex items-center gap-[22px]">
        {portfolioValue != null && (
          <div className="hidden flex-col items-end gap-[3px] sm:flex">
            <span className="text-[10px] uppercase text-ink-faint" style={{ letterSpacing: '.1em' }}>
              Net liq
            </span>
            <span className="tabular text-[15px] font-medium leading-none">{fmtUSD(portfolioValue)}</span>
          </div>
        )}
        {dayPnl != null && (
          <div className="hidden flex-col items-end gap-[3px] sm:flex">
            <span className="text-[10px] uppercase text-ink-faint" style={{ letterSpacing: '.1em' }}>
              Day
            </span>
            <span
              className="tabular text-[15px] font-medium leading-none"
              style={{ color: dayPnl >= 0 ? C.bright : C.danger }}
            >
              {fmtSignedUSD(dayPnl)}
            </span>
          </div>
        )}
        <button onClick={onKill} className="mf-btn-danger" title="Emergency kill switch (Cmd+Shift+H)">
          Kill switch
        </button>
        <button
          onClick={onBell}
          title="Alerts"
          className="relative flex h-8 w-8 items-center justify-center rounded-lg text-ink-muted hover:bg-ink/[.06] hover:text-ink"
        >
          <Bell size={16} />
          {alertCount > 0 && (
            <span className="absolute -right-1 -top-1 flex h-[16px] min-w-[16px] items-center justify-center rounded-full border-2 border-base bg-signal-red px-1 text-[10px] font-bold text-white">
              {alertCount}
            </span>
          )}
        </button>
        <button
          onClick={onToggleAsk}
          title="Toggle Ask AI panel"
          className="flex h-8 w-8 items-center justify-center rounded-lg text-ink-muted hover:bg-ink/[.06] hover:text-ink"
        >
          <PanelRightClose size={16} />
        </button>
      </div>
    </header>
  )
}
