import { type ComponentType } from 'react'
import {
  LayoutDashboard,
  CheckCircle2,
  TrendingUp,
  ShieldCheck,
  PlugZap,
  Globe,
  Route,
} from 'lucide-react'
import type { Tab } from '../Dashboard'

interface NavItem {
  tab: Tab
  label: string
  icon: ComponentType<{ size?: number }>
  badge?: number
}

interface Props {
  active: Tab
  pendingCount: number
  onNavigate: (tab: Tab) => void
}

const NAV: NavItem[] = [
  { tab: 'dashboard', label: 'Dashboard', icon: LayoutDashboard },
  { tab: 'green-light', label: 'Green Light', icon: CheckCircle2 },
  { tab: 'positions', label: 'Positions', icon: TrendingUp },
  { tab: 'fortress', label: 'Fortress', icon: ShieldCheck },
  { tab: 'execution', label: 'Execution', icon: PlugZap },
  { tab: 'ops', label: 'Ops', icon: Globe },
  { tab: 'roadmap', label: 'Roadmap', icon: Route },
]

export function Sidebar({ active, pendingCount, onNavigate }: Props) {
  return (
    <aside
      className="flex h-screen w-[232px] shrink-0 flex-col gap-[22px] border-r border-ink/10 bg-base py-[22px] sticky top-0"
    >
      {/* Brand */}
      <div className="flex flex-col gap-[6px] px-[17px]">
        <span className="text-[17px] font-medium tracking-[-0.02em]">MarketFlow</span>
        <div className="flex items-center gap-1.5">
          <span className="mf-tag-outline">v2.4</span>
          <span className="font-mono text-[11px] text-ink-faint">equities · Alpaca</span>
        </div>
      </div>

      {/* Nav — flat, no groups */}
      <nav className="mf-scroll flex flex-col gap-[2px] overflow-y-auto px-[8px]">
        {NAV.map((it) => {
          const Icon = it.icon
          const isActive = active === it.tab
          const badge = it.tab === 'green-light' ? pendingCount : it.badge
          return (
            <button
              key={it.tab}
              onClick={() => onNavigate(it.tab)}
              className={`flex items-center gap-2.5 rounded-lg px-2.5 py-2 text-left text-[13.5px] transition-colors hover:bg-ink/[.06] ${
                isActive ? 'text-ink' : 'text-ink-muted'
              }`}
              style={isActive ? { background: 'rgba(145,132,217,.18)' } : undefined}
            >
              <Icon size={16} />
              <span className="flex-1 truncate">{it.label}</span>
              {badge != null && badge > 0 && (
                <span className="rounded-[5px] bg-accent-ring px-1.5 py-[3px] font-mono text-[10px] font-medium leading-none text-[#e7e5fe]">
                  {badge}
                </span>
              )}
            </button>
          )
        })}
      </nav>

      {/* Footer */}
      <div className="mt-auto flex flex-col gap-[8px] px-[17px]">
        <div className="mf-hairline" />
        <div className="font-mono text-[11px] leading-[1.5] text-ink-faint">
          operator · ron
          <br />
          Asia/Jerusalem · logs UTC
        </div>
      </div>
    </aside>
  )
}
