import type { ReactNode } from 'react'
import type { Direction } from '../../types'

// ─── Card ─────────────────────────────────────────────────────────────────────

export function Card({ className = '', children }: { className?: string; children: ReactNode }) {
  return <div className={`mf-card ${className}`}>{children}</div>
}

// ─── Page header (eyebrow + 28px headline) ────────────────────────────────────

export function PageHead({
  eyebrow,
  title,
  right,
}: {
  eyebrow: string
  title: ReactNode
  right?: ReactNode
}) {
  return (
    <div className="flex flex-wrap items-end gap-4">
      <div className="mr-auto">
        <div className="mf-eyebrow mb-2">{eyebrow}</div>
        <h2 className="m-0 text-[28px] font-medium tracking-[-0.02em]">{title}</h2>
      </div>
      {right}
    </div>
  )
}

// ─── Section eyebrow header ───────────────────────────────────────────────────

export function SectionHead({ eyebrow, note }: { eyebrow: string; note?: string }) {
  return (
    <div className="flex items-baseline gap-3">
      <span className="mf-eyebrow">{eyebrow}</span>
      {note && <span className="font-mono text-[12px] text-ink-faint">{note}</span>}
    </div>
  )
}

// ─── Small-print source citation ──────────────────────────────────────────────

export function Source({ children }: { children: ReactNode }) {
  return (
    <div className="break-all font-mono text-[11px] leading-[1.5] text-ink-faint">
      source · {children}
    </div>
  )
}

// ─── Direction badge (BUY/SELL/SHORT/COVER + LONG) ────────────────────────────

export function DirectionBadge({ dir }: { dir: Direction | 'LONG' }) {
  const buy = dir === 'BUY' || dir === 'COVER' || dir === 'LONG'
  return (
    <span
      className={`mf-chip ${buy ? 'bg-signal-green/15 text-signal-green' : 'bg-signal-red/15 text-signal-red'}`}
    >
      {dir}
    </span>
  )
}

// ─── Confidence bar (value 0..1) ──────────────────────────────────────────────

export function ConfidenceBar({ value, className = '' }: { value: number; className?: string }) {
  const pct = Math.round(value * 100)
  const color = value >= 0.7 ? 'bg-signal-green' : value >= 0.55 ? 'bg-signal-yellow' : 'bg-signal-red'
  return (
    <div className={`h-1.5 w-full overflow-hidden rounded bg-base ${className}`}>
      <div className={`h-full ${color} transition-[width]`} style={{ width: `${pct}%` }} />
    </div>
  )
}

// ─── Status dot ───────────────────────────────────────────────────────────────

export function StatusDot({
  color = '#b5abfc',
  pulse = false,
  size = 8,
}: {
  color?: string
  pulse?: boolean
  size?: number
}) {
  return (
    <span
      className={`inline-block rounded-full ${pulse ? 'animate-pulse-dot' : ''}`}
      style={{ width: size, height: size, background: color, boxShadow: `0 0 8px ${color}` }}
    />
  )
}

// ─── Status pill ──────────────────────────────────────────────────────────────

export function Pill({
  children,
  tone = 'neutral',
  onClick,
  title,
  className = '',
}: {
  children: ReactNode
  tone?: 'neutral' | 'green' | 'red' | 'orange' | 'yellow' | 'blue' | 'purple'
  onClick?: () => void
  title?: string
  className?: string
}) {
  const tones: Record<string, string> = {
    neutral: 'bg-surface-sunken border-line-faint text-ink-faint',
    green: 'bg-signal-green/10 border-signal-green/25 text-signal-green',
    red: 'bg-signal-red/12 border-signal-red/30 text-signal-red',
    orange: 'bg-signal-orange/12 border-signal-orange/30 text-signal-orange',
    yellow: 'bg-signal-yellow/14 border-signal-yellow/40 text-signal-yellow',
    blue: 'bg-signal-blue/12 border-signal-blue/30 text-signal-blue',
    purple: 'bg-signal-purple/14 border-signal-purple/35 text-signal-purple',
  }
  const Tag = onClick ? 'button' : 'div'
  return (
    <Tag
      onClick={onClick}
      title={title}
      className={`mf-pill border font-medium shrink-0 ${tones[tone]} ${onClick ? 'cursor-pointer' : ''} ${className}`}
    >
      {children}
    </Tag>
  )
}

// ─── Severity badge (alerts) ──────────────────────────────────────────────────

export type Severity = 'CRITICAL' | 'HIGH' | 'MEDIUM' | 'INFO'

const SEVERITY: Record<Severity, { chip: string; glow: string }> = {
  CRITICAL: { chip: 'bg-signal-red/18 text-signal-red', glow: 'rgba(217,123,132,.4)' },
  HIGH: { chip: 'bg-signal-orange/18 text-signal-orange', glow: 'rgba(217,160,91,.35)' },
  MEDIUM: { chip: 'bg-signal-yellow/18 text-signal-yellow', glow: 'rgba(217,160,91,.3)' },
  INFO: { chip: 'bg-ink-muted/15 text-ink-muted', glow: '#3f424d' },
}

export function SeverityBadge({ severity }: { severity: Severity }) {
  return (
    <span className={`mf-chip ${SEVERITY[severity].chip}`}>{severity}</span>
  )
}
