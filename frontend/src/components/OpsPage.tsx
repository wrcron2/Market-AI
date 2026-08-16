import { useMemo } from 'react'
import { PageHead, SectionHead, Source, Card } from './ui/primitives'
import { AutoExecuteToggle } from './AutoExecuteToggle'
import { TradingModeToggle, type TradingMode } from './TradingModeToggle'
import { LLMProviderToggle } from './LLMProviderToggle'
import { AlertsPanel } from './AlertsPanel'
import { useMarketStatus } from '../hooks/useMarketStatus'
import { C } from '../lib/format'

interface Props {
  mode: TradingMode
  onModeChange: (mode: TradingMode) => void
  autoExec: boolean
  onAutoExecChange: (enabled: boolean) => void
}

/** Wall-clock h:mm in America/New_York today, returned as a real Date. */
function etTimeToday(h: number, m: number): Date {
  const now = new Date()
  const etNow = new Date(now.toLocaleString('en-US', { timeZone: 'America/New_York' }))
  const shift = now.getTime() - etNow.getTime()
  const t = new Date(etNow)
  t.setHours(h, m, 0, 0)
  return new Date(t.getTime() + shift)
}

const fmtIL = (d: Date) =>
  d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'Asia/Jerusalem' })
const fmtET = (d: Date) =>
  d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', timeZone: 'America/New_York' })

/** Ops — daily cycle (IL clock), session state, configuration, alerts. */
export function OpsPage({ mode, onModeChange, autoExec, onAutoExecChange }: Props) {
  const { isOpen, minutesUntilClose, minutesUntilOpen } = useMarketStatus()

  const { openT, closeT, nowPct } = useMemo(() => {
    const open = etTimeToday(9, 30)
    const close = etTimeToday(16, 0)
    const now = Date.now()
    const pct = Math.min(100, Math.max(0, ((now - open.getTime()) / (close.getTime() - open.getTime())) * 100))
    return { openT: open, closeT: close, nowPct: pct }
  }, [])

  return (
    <div className="flex flex-col gap-[22px]">
      <PageHead eyebrow="Operations" title="Daily cycle, session, configuration" />

      {/* Daily cycle — regular session rendered on the Asia/Jerusalem clock */}
      <div className="mf-card flex flex-col gap-[22px] px-7 pb-7 pt-[22px]">
        <SectionHead eyebrow="Daily cycle · Asia/Jerusalem" note="regular session 09:30–16:00 ET" />
        <div className="relative h-[96px]">
          <div className="mf-hairline absolute left-0 right-0 top-[46px]" style={{ height: 2 }} />
          {isOpen && (
            <div
              className="absolute left-0 top-[46px] h-[2px] bg-accent"
              style={{ width: `${nowPct}%` }}
            />
          )}
          {/* open */}
          <TimelineDot left="0%" active={isOpen} label={`${fmtIL(openT)}`} sub={`US open · ${fmtET(openT)} ET`} above />
          {/* now — only meaningful inside the session */}
          {isOpen && (
            <TimelineDot left={`${nowPct}%`} now label={fmtIL(new Date())} sub="now" above={nowPct > 50} />
          )}
          {/* close */}
          <TimelineDot left="100%" label={fmtIL(closeT)} sub={`close · ${fmtET(closeT)} ET`} above right />
        </div>
        <span className="font-mono text-[11px] leading-[1.5] text-ink-faint">
          market status computed locally from the ET session clock (hooks/useMarketStatus.ts) —
          no exchange calendar, holidays not modeled
        </span>
      </div>

      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[1.2fr_1fr]">
        {/* Session status */}
        <div className="mf-card flex flex-col gap-2.5 p-[22px]">
          <span className="mf-eyebrow">Session status</span>
          <span
            className="text-[40px] font-medium leading-none tracking-[-0.03em]"
            style={{ color: isOpen ? C.bright : C.muted }}
          >
            {isOpen ? 'Open' : 'Closed'}
          </span>
          <span className="font-mono text-[12px] leading-[1.6] text-ink-muted">
            {isOpen && minutesUntilClose != null && `${minutesUntilClose} min to close · `}
            {!isOpen && minutesUntilOpen != null && `opens in ${Math.floor(minutesUntilOpen / 60)}h ${minutesUntilOpen % 60}m · `}
            mode {mode === 'ibkr' ? 'alpaca paper' : 'yahoo sim'} · auto-execute {autoExec ? 'on' : 'off'}
          </span>
          <div className="mf-hairline" />
          <span className="font-mono text-[12px] leading-[1.5] text-ink-faint">
            execution venue is Alpaca paper — nothing reaches a broker without the Green Light
            gate or AUTO_EXECUTE
          </span>
        </div>

        {/* v2.4 ops track note */}
        <div className="mf-card flex flex-col gap-2.5 p-[22px]">
          <span className="mf-eyebrow">v2.4 ops layer</span>
          <span className="font-mono text-[12px] leading-[1.7] text-ink-muted">
            DST-safe scheduler · corporate-actions daily job · W-8BEN monitor · Israeli
            semi-annual tax exports · TCA friction monitor (CRITICAL at &gt;2 bps excess / 20
            trades)
          </span>
          <span className="font-mono text-[12px] leading-[1.5] text-ink-faint">
            tracked in ops/ at the repo root · deliberately standalone — not wired to live UI data
            yet
          </span>
          <Source>ops/ (repo root) · AGENTS.md §ops layer</Source>
        </div>
      </div>

      <div className="mf-hairline" />
      <SectionHead eyebrow="Configuration" note="every change is written to the audit trail" />
      <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-3">
        <Card className="p-[22px]">
          <AutoExecuteToggle enabled={autoExec} onChange={onAutoExecChange} />
        </Card>
        <Card className="p-[22px]">
          <div className="mf-eyebrow mb-3">Trading mode</div>
          <TradingModeToggle mode={mode} onChange={onModeChange} />
        </Card>
        <Card className="p-[22px]">
          <div className="mf-eyebrow mb-3">LLM provider</div>
          <LLMProviderToggle />
        </Card>
      </div>

      <div className="mf-hairline" />
      <SectionHead eyebrow="Alerts" note="risk and system events" />
      <AlertsPanel />
    </div>
  )
}

function TimelineDot({
  left,
  label,
  sub,
  active = false,
  now = false,
  above = false,
  right = false,
}: {
  left: string
  label: string
  sub: string
  active?: boolean
  now?: boolean
  above?: boolean
  right?: boolean
}) {
  const ring = now ? C.ink : active ? C.accent : C.line
  const size = now ? 20 : 14
  return (
    <>
      <div
        className="absolute rounded-full bg-base"
        style={{
          left,
          top: 46 - size / 2 + 1,
          width: size,
          height: size,
          transform: right ? 'translateX(-100%)' : undefined,
          boxShadow: `0 0 0 2px ${ring}`,
        }}
      />
      <div
        className="absolute whitespace-nowrap font-mono text-[12px] leading-[1.4]"
        style={{
          left: right ? undefined : left,
          right: right ? 0 : undefined,
          textAlign: right ? 'right' : 'left',
          ...(above ? { top: 0 } : { top: 66 }),
          color: now ? C.ink : C.muted,
          fontFamily: now ? 'Inter, sans-serif' : undefined,
          fontWeight: now ? 500 : 400,
        }}
      >
        {label}
        <br />
        <span style={{ color: C.faint }}>{sub}</span>
      </div>
    </>
  )
}
