import { PageHead, SectionHead, Source } from './ui/primitives'
import { ReportsPanel } from './ReportsPanel'

interface Props {
  eodRefreshToken: number
}

/**
 * Fortress — strategy validation. The three verdict cards are the real v2.4
 * gate results (G0/G1 from committed result files, G2 pending); below them is
 * the live strategy-reports stack fed by /api/reports/*.
 */
export function FortressPage({ eodRefreshToken }: Props) {
  return (
    <div className="flex flex-col gap-[22px]">
      <PageHead eyebrow="Validation fortress" title="No capital before a verdict" />

      <div className="grid grid-cols-1 gap-4 lg:grid-cols-3">
        {/* G0 — PASS */}
        <div
          className="flex flex-col gap-3 rounded-lg p-[22px]"
          style={{ boxShadow: '0 0 0 1px #423a6a', background: 'rgba(145,132,217,.07)' }}
        >
          <div className="flex items-center gap-2">
            <span className="text-[17px] font-medium">G0 · Item Zero</span>
            <span className="mf-tag-accent ml-auto">PASS</span>
          </div>
          <span className="tabular text-[40px] font-medium leading-none tracking-[-0.03em]">
            70.98%
          </span>
          <span className="font-mono text-[12px] leading-[1.5] text-ink-muted">
            net annual, monthly M1 cadence · 95% CI floor +32.31%
            <br />
            weekly cadence 59.61% · CI floor +1.21%
          </span>
          <div className="mf-hairline" />
          <span className="font-mono text-[12px] leading-[1.5] text-ink-faint">
            24 rotations / 110 trades (monthly) · friction drag 0.72%/yr · passed both bars: CI
            floor &gt; 0 and net &gt; 2× friction
          </span>
          <Source>item_zero_results.csv (repo root) · memo 2026-08-03</Source>
        </div>

        {/* G1 — PASS */}
        <div className="mf-card flex flex-col gap-3 p-[22px]">
          <div className="flex items-center gap-2">
            <span className="text-[17px] font-medium">G1 · Fortress reproduces Item Zero</span>
            <span className="mf-tag-accent ml-auto">PASS</span>
          </div>
          <span className="tabular text-[40px] font-medium leading-none tracking-[-0.03em]">
            122/122
          </span>
          <span className="font-mono text-[12px] leading-[1.5] text-ink-muted">
            tests passing, verified 2026-08-15
            <br />
            fortress verdicts match Item Zero within rounding
          </span>
          <div className="mf-hairline" />
          <span className="font-mono text-[12px] leading-[1.5] text-ink-faint">
            m1 monthly: pass · m1 weekly: pass — shared cost model is the single friction code path
          </span>
          <Source>fortress_verdict_table.csv · tests/test_fortress_reproduces_item_zero.py</Source>
        </div>

        {/* G2 — PENDING */}
        <div className="mf-card flex flex-col gap-3 p-[22px]">
          <div className="flex items-center gap-2">
            <span className="text-[17px] font-medium">G2 · Chaos / resync</span>
            <span className="mf-tag-outline ml-auto">PENDING</span>
          </div>
          <span className="text-[40px] font-medium leading-none tracking-[-0.03em] text-ink-muted">
            not run
          </span>
          <span className="font-mono text-[12px] leading-[1.5] text-ink-muted">
            kill the VM mid-trade (position open, journal unflushed), restart: system state =
            broker state, zero naked positions
          </span>
          <div className="mf-hairline" />
          <span className="font-mono text-[12px] leading-[1.5] text-ink-faint">
            due end of week 7 · failure → fix the execution lane, no paper trading
          </span>
          <Source>docs/gates_kill_criteria.md §9</Source>
        </div>
      </div>

      <div className="mf-hairline" />

      <SectionHead
        eyebrow="Strategy reports · live"
        note="per-strategy stats, outcome accuracy, EOD — /api/reports/*"
      />
      <ReportsPanel eodRefreshToken={eodRefreshToken} />
    </div>
  )
}
