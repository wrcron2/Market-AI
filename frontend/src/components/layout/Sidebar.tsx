import { type ComponentType } from 'react'
import {
  Activity,
  TrendingUp,
  GitBranch,
  Bell,
  ScrollText,
  Boxes,
  SlidersHorizontal,
  ChevronsLeft,
} from 'lucide-react'
import type { Tab } from '../Dashboard'
import type { TradingMode } from '../TradingModeToggle'
import { StatusDot } from '../ui/primitives'

interface NavItem {
  tab: Tab
  label: string
  icon: ComponentType<{ size?: number }>
  badge?: number
}
interface NavGroup {
  label: string
  items: NavItem[]
}

interface Props {
  collapsed: boolean
  active: Tab
  pendingCount: number
  mode: TradingMode
  onNavigate: (tab: Tab) => void
  onToggle: () => void
}

export function Sidebar({ collapsed, active, pendingCount, mode, onNavigate, onToggle }: Props) {
  const groups: NavGroup[] = [
    { label: 'Trading', items: [{ tab: 'signals', label: 'Live Signals', icon: Activity, badge: pendingCount }] },
    { label: 'Performance', items: [{ tab: 'portfolio', label: 'Alpaca Portfolio', icon: TrendingUp }] },
    { label: 'AI Pipeline', items: [{ tab: 'pipeline', label: 'Pipeline', icon: GitBranch }] },
    {
      label: 'Monitoring',
      items: [
        { tab: 'alerts', label: 'Alerts', icon: Bell },
        { tab: 'audit', label: 'Audit Log', icon: ScrollText },
      ],
    },
    {
      label: 'System',
      items: [
        { tab: 'versions', label: 'Versions & Deploy', icon: Boxes },
        { tab: 'config', label: 'Configuration', icon: SlidersHorizontal },
      ],
    },
  ]

  return (
    <aside
      className="flex h-full shrink-0 flex-col border-r border-line-faint bg-base transition-[width] duration-200"
      style={{ width: collapsed ? 64 : 240 }}
    >
      {/* Brand */}
      <div className="flex h-[60px] shrink-0 items-center gap-2.5 border-b border-line-faint px-4">
        <div className="flex h-[30px] w-[30px] shrink-0 items-center justify-center rounded-lg bg-gradient-to-br from-signal-blue to-signal-purple text-[15px] font-bold text-white">
          M
        </div>
        {!collapsed && (
          <div className="flex flex-col leading-tight">
            <span className="text-[15px] font-semibold tracking-tight">MarketFlow</span>
            <span className="text-[10px] font-medium tracking-[0.16em] text-ink-faint">AI · AUTONOMOUS</span>
          </div>
        )}
      </div>

      {/* Nav */}
      <nav className="mf-scroll flex-1 overflow-y-auto overflow-x-hidden px-2.5 py-2.5">
        {groups.map((g) => (
          <div key={g.label}>
            {!collapsed && (
              <div className="px-2 pb-1.5 pt-3.5 text-[10px] font-semibold uppercase tracking-[0.14em] text-slate-600">
                {g.label}
              </div>
            )}
            {g.items.map((it) => {
              const Icon = it.icon
              const isActive = active === it.tab
              return (
                <button
                  key={it.tab}
                  onClick={() => onNavigate(it.tab)}
                  title={it.label}
                  className={`relative mb-0.5 flex w-full items-center gap-2.5 rounded-lg px-2.5 py-2 text-[13px] font-medium transition-colors ${
                    isActive
                      ? 'bg-signal-blue/10 text-blue-200'
                      : 'text-slate-300 hover:bg-surface-raised'
                  }`}
                >
                  {isActive && (
                    <span className="absolute left-0 top-1.5 bottom-1.5 w-[3px] rounded-sm bg-signal-blue" />
                  )}
                  <span className={`flex w-[18px] shrink-0 items-center justify-center ${isActive ? 'text-blue-400' : 'text-ink-faint'}`}>
                    <Icon size={16} />
                  </span>
                  {!collapsed && <span className="flex-1 truncate text-left">{it.label}</span>}
                  {!collapsed && !!it.badge && it.badge > 0 && (
                    <span className="flex h-[18px] min-w-[18px] animate-pulse-dot items-center justify-center rounded-full bg-signal-yellow px-1.5 text-[10px] font-bold text-base">
                      {it.badge}
                    </span>
                  )}
                </button>
              )
            })}
          </div>
        ))}
      </nav>

      {/* Footer status */}
      <div className="flex shrink-0 flex-col gap-1.5 border-t border-line-faint p-2.5">
        <div className="flex items-center gap-2 rounded-lg bg-surface-sunken px-2 py-1.5">
          <StatusDot color="#22c55e" />
          {!collapsed && <span className="text-xs text-ink-muted">Market Open</span>}
        </div>
        <div className="flex items-center gap-2 rounded-lg bg-surface-sunken px-2 py-1.5">
          <span
            className="h-2 w-2 shrink-0 rounded-sm"
            style={{ background: mode === 'ibkr' ? '#ef4444' : '#3b82f6' }}
          />
          {!collapsed && (
            <span className="text-xs text-ink-muted">{mode === 'ibkr' ? 'IBKR · Live' : 'Yahoo · Sim'}</span>
          )}
        </div>
        {!collapsed && (
          <button
            onClick={onToggle}
            className="mt-1 flex items-center gap-2 rounded-lg px-2 py-1.5 text-xs text-ink-faint hover:text-ink"
          >
            <ChevronsLeft size={14} /> Collapse
          </button>
        )}
      </div>
    </aside>
  )
}
