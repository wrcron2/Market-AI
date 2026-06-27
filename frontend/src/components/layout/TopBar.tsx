import { Menu, Octagon, Bell, Settings, PanelRightClose, Zap, Pause, AlertTriangle } from 'lucide-react'
import { Pill, StatusDot } from '../ui/primitives'
import { fmtUSD } from '../../lib/format'
import type { TradingMode } from '../TradingModeToggle'

interface Props {
  breadcrumb: string
  wsConnected: boolean
  autoExec: boolean
  mode: TradingMode
  portfolioValue: number | null
  marketOpen: boolean
  marketMinutes: number | null
  alertCount?: number
  llmDegraded?: boolean
  onToggleNav: () => void
  onToggleAsk: () => void
  onHalt: () => void
  onAutoClick: () => void
  onBell: () => void
  onSettings: () => void
}

export function TopBar({
  breadcrumb,
  wsConnected,
  autoExec,
  mode,
  portfolioValue,
  marketOpen,
  marketMinutes,
  alertCount = 0,
  llmDegraded = false,
  onToggleNav,
  onToggleAsk,
  onHalt,
  onAutoClick,
  onBell,
  onSettings,
}: Props) {
  return (
    <header className="z-30 flex h-[60px] shrink-0 items-center gap-3.5 border-b border-line-faint bg-base px-4">
      {/* Left */}
      <button
        onClick={onToggleNav}
        title="Toggle navigation"
        className="flex h-[34px] w-[34px] shrink-0 items-center justify-center rounded-lg border border-line bg-surface-hover text-ink-muted hover:bg-[#222d42] hover:text-ink"
      >
        <Menu size={17} />
      </button>
      <div className="flex shrink-0 items-center gap-1.5 text-[13px]">
        <span className="hidden text-ink-faint sm:inline">MarketFlow</span>
        <span className="hidden text-line sm:inline">/</span>
        <span className="font-semibold text-ink">{breadcrumb}</span>
      </div>

      {/* Center status pills */}
      <div className="flex min-w-0 flex-1 flex-wrap items-center justify-center gap-1.5">
        {llmDegraded && (
          <Pill tone="red" className="animate-pulse">
            <AlertTriangle size={13} />
            DEGRADED — Ollama Fallback
          </Pill>
        )}
        <Pill tone={marketOpen ? 'green' : 'neutral'} className="hidden md:inline-flex">
          <StatusDot color={marketOpen ? '#22c55e' : '#6b7280'} />
          {marketOpen ? 'Market Open' : 'Market Closed'}
          {marketOpen && marketMinutes != null && (
            <span className="hidden font-mono text-[11px] text-ink-faint lg:inline">
              · closes {marketMinutes}m
            </span>
          )}
        </Pill>

        <Pill tone={wsConnected ? 'green' : 'orange'} className="hidden md:inline-flex">
          <StatusDot color={wsConnected ? '#22c55e' : '#f97316'} pulse={!wsConnected} />
          {wsConnected ? 'Connected' : 'Reconnecting…'}
        </Pill>

        <Pill
          tone={autoExec ? 'yellow' : 'neutral'}
          onClick={onAutoClick}
          title="Read-only · click to open Configuration"
          className="hidden md:inline-flex"
        >
          {autoExec ? <Zap size={13} /> : <Pause size={13} />}
          {autoExec ? 'AUTO ON' : 'AUTO OFF'}
        </Pill>

        <Pill tone={mode === 'ibkr' ? 'red' : 'blue'} className="hidden lg:inline-flex">
          {mode === 'ibkr' ? 'IBKR LIVE' : 'YAHOO SIM'}
        </Pill>

        {portfolioValue != null && (
          <Pill className="hidden xl:inline-flex">
            <span className="text-[11px] text-ink-faint">Portfolio</span>
            <span className="font-mono text-[12px] font-semibold tabular text-ink">{fmtUSD(portfolioValue)}</span>
          </Pill>
        )}
      </div>

      {/* Right */}
      <div className="flex shrink-0 items-center gap-2">
        <button
          onClick={onHalt}
          title="Emergency kill switch — disables AUTO & cancels orders (Cmd+Shift+H)"
          className="flex items-center gap-1.5 rounded-lg border border-signal-red bg-signal-red px-3.5 py-2 text-[12.5px] font-bold tracking-wide text-white hover:animate-glow"
        >
          <Octagon size={15} /> HALT ALL
        </button>
        <button
          onClick={onBell}
          title="Alerts"
          className="relative flex h-9 w-9 items-center justify-center rounded-lg border border-line bg-surface-hover text-ink-muted hover:bg-[#222d42] hover:text-ink"
        >
          <Bell size={17} />
          {alertCount > 0 && (
            <span className="absolute -right-1.5 -top-1.5 flex h-[17px] min-w-[17px] items-center justify-center rounded-full border-2 border-base bg-signal-red px-1 text-[10px] font-bold text-white">
              {alertCount}
            </span>
          )}
        </button>
        <button
          onClick={onSettings}
          title="Configuration"
          className="flex h-9 w-9 items-center justify-center rounded-lg border border-line bg-surface-hover text-ink-muted hover:bg-[#222d42] hover:text-ink"
        >
          <Settings size={17} />
        </button>
        <button
          onClick={onToggleAsk}
          title="Toggle Ask AI panel"
          className="flex h-9 w-9 items-center justify-center rounded-lg border border-line bg-surface-hover text-ink-muted hover:bg-[#222d42] hover:text-ink"
        >
          <PanelRightClose size={17} />
        </button>
      </div>
    </header>
  )
}
