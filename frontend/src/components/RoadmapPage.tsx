import type { ReactNode } from 'react'
import { PageHead, SectionHead, Source } from './ui/primitives'

type GateStatus = 'PASS' | 'NEXT' | 'GATED'

interface Gate {
  id: string
  timing: string
  summary: string
  exit: string
  status: GateStatus
  evidence?: string
}

// Real statuses per docs/gates_kill_criteria.md §9 and the committed result files.
const GATES: Gate[] = [
  {
    id: 'G0',
    timing: 'End wk 1',
    summary:
      'Item Zero memo: at least one M1 rotation cadence clears both bars — net annualized expectancy positive with bootstrap CI lower bound > 0, and net expectancy > 2× modeled annual friction drag.',
    exit: 'exit: memo exists with both bars cleared · else park the trading build',
    status: 'PASS',
    evidence: 'passed 2026-08-03 · item_zero_results.csv — monthly net 70.98%/yr (CI floor 32.31%), weekly 59.61% (floor 1.21%)',
  },
  {
    id: 'G1',
    timing: 'End wk 3',
    summary:
      'Fortress reproduces Item Zero\u2019s numbers within rounding; hurdles and CI widths pre-registered in writing.',
    exit: 'exit: reproduction within rounding · else fix the data layer, do not touch the execution lane',
    status: 'PASS',
    evidence: 'passed 2026-08-15 · 122/122 tests · fortress_verdict_table.csv',
  },
  {
    id: 'G2',
    timing: 'End wk 7',
    summary:
      'Chaos test — kill the VM mid-trade (position open, brackets live, journal unflushed), restart: system state = broker state, zero naked positions, journal gap explained.',
    exit: 'exit: clean reconstruction · else fix the execution lane; no paper trading',
    status: 'NEXT',
  },
  {
    id: 'G3',
    timing: 'End wk 10',
    summary: 'Four clean paper weeks on M1+M2; realized friction inside cost-model bands.',
    exit: 'exit: clean paper record · else diagnose cost model vs reality; extend paper',
    status: 'GATED',
  },
  {
    id: 'G4',
    timing: 'Wk 11→14',
    summary:
      'Live pilot ($1,100, M1 only) survives one month under the kill criteria below with zero triggers.',
    exit: 'exit: one month, zero triggers · else halt per the kill criteria; post-mortem to the intent journal',
    status: 'GATED',
  },
  {
    id: 'G5',
    timing: 'Wk 16',
    summary:
      'D1 decision memo names exactly one of A (re-cut, $80+ universe, ~6 bps gross hurdle) / B (park) / C (drop, reallocate to M1/M2), hurdle restated beside it.',
    exit: 'exit: decision memo filed · default is B (park) if the evidence is ambiguous',
    status: 'GATED',
  },
]

const KILL_CRITERIA = [
  'Drawdown breach — sleeve drawdown beyond the fortress\u2019s bootstrap 95th percentile.',
  'Friction breach — realized friction exceeding the cost model by >2 bps over any 20-trade window.',
  'Resync failure — any startup/reconnect resync that cannot reconstruct broker state exactly (positions, open orders, bracket coverage).',
  'Orphaned bracket — any orphaned bracket left after a corporate action (cancel/rebuild guard around ex-dates failed).',
]

const TAG: Record<GateStatus, ReactNode> = {
  PASS: <span className="mf-tag-neutral justify-self-end">PASS</span>,
  NEXT: <span className="mf-tag-accent justify-self-end">NEXT</span>,
  GATED: <span className="mf-tag-outline justify-self-end">GATED</span>,
}

/** Roadmap — the binding §9 gate plan with real statuses. Static, cited. */
export function RoadmapPage() {
  return (
    <div className="flex flex-col gap-[22px]">
      <PageHead eyebrow="Roadmap · gates G0–G5" title="Written in advance, binding" />

      <div className="flex flex-col gap-3">
        {GATES.map((g) => {
          const current = g.status === 'NEXT'
          return (
            <div
              key={g.id}
              className="grid grid-cols-1 items-center gap-4 rounded-lg p-[22px] md:grid-cols-[100px_1fr_250px_76px] md:gap-4"
              style={{
                boxShadow: current ? '0 0 0 1px #423a6a' : '0 0 0 1px #3f424d',
                background: current ? 'rgba(145,132,217,.07)' : '#232532',
                opacity: g.status === 'PASS' ? 0.62 : 1,
              }}
            >
              <div className="flex flex-col gap-1">
                <span
                  className={`font-medium leading-none ${current ? 'text-[34px] text-signal-green' : 'text-[24px]'}`}
                >
                  {g.id}
                </span>
                <span className="font-mono text-[11px] text-ink-faint">{g.timing}</span>
              </div>
              <div className="flex flex-col gap-2">
                <span className="text-[13.5px] leading-[1.5]">{g.summary}</span>
                {g.evidence && (
                  <span className="font-mono text-[11px] leading-[1.5] text-signal-green">
                    {g.evidence}
                  </span>
                )}
              </div>
              <span className="font-mono text-[12px] leading-[1.5] text-ink-muted">{g.exit}</span>
              {TAG[g.status]}
            </div>
          )
        })}
      </div>

      <div className="mf-card flex flex-col gap-3 p-[22px]">
        <SectionHead
          eyebrow="Live-pilot kill criteria"
          note="pre-set at G3, binding at G4 · any single trigger halts the sleeve"
        />
        <ol className="m-0 flex list-none flex-col gap-2 p-0">
          {KILL_CRITERIA.map((k, i) => (
            <li key={i} className="flex gap-3 font-mono text-[12.5px] leading-[1.6] text-ink-muted">
              <span className="text-ink-faint">{i + 1}.</span>
              <span>{k}</span>
            </li>
          ))}
        </ol>
        <div className="mf-hairline" />
        <span className="font-mono text-[12px] leading-[1.5] text-ink-faint">
          none of these require judgment in the moment — that is the entire point of writing them
          now. A halted sleeve stays halted until the post-mortem passes the failed gate again.
        </span>
        <Source>docs/gates_kill_criteria.md §9 (issued 2026-08-03)</Source>
      </div>
    </div>
  )
}
