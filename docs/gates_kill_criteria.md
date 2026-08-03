# Section 9 — Gates G0–G5 and Pre-Set Live-Pilot Kill Criteria

**Issued:** 2026-08-03 · **Source:** Implementation Plan §9 (Gates, kill criteria, and
timeline). **Status:** written in advance, binding. No gate may be renegotiated mid-phase.

**Objective.** Make stopping as well-specified as starting. Every gate below is binary and
written before the phase begins; the live-pilot kill criteria exist before the first real
order. The project should fail cheaply at the right layer instead of expensively at the last
one.

## Phase gates

| Gate | Timing | Pass condition | If failed |
|---|---|---|---|
| **G0** | End week 1 | Item Zero memo exists: at least one M1 rotation cadence clears both bars — (a) net annualized expectancy positive with bootstrap CI lower bound > 0, and (b) net expectancy > 2× modeled annual friction drag | Park the trading build; keep only the ops/tax tooling. This is a success outcome for Item Zero, not a failure |
| **G1** | End week 3 | Fortress reproduces Item Zero's numbers within rounding; hurdles and CI widths pre-registered in writing | Fix the data layer; do not touch the execution lane |
| **G2** | End week 7 | Chaos test passes — kill the VM mid-trade (position open, brackets live, journal unflushed), restart: system state = broker state, zero naked positions, journal gap explained | Fix the execution lane; no paper trading |
| **G3** | End week 10 | 4 clean paper weeks on M1+M2; realized friction inside cost-model bands | Diagnose cost model vs reality; extend paper |
| **G4** | Weeks 11→14 | Live pilot ($1,100, M1 only) survives 1 month under the kill criteria below with zero triggers | Halt per the kill criteria; post-mortem written to the intent journal |
| **G5** | Week 16 | D1 decision memo names exactly one of A (re-cut, $80+ universe, ~6 bps gross hurdle) / B (park) / C (drop, reallocate to M1/M2), with the hurdle restated beside it | Default is **B (park)** if the evidence is ambiguous |

## Live-pilot kill criteria (pre-set at G3, binding at G4)

Any **single** trigger below halts the sleeve immediately, flattens per the envelope rules,
and opens a post-mortem in the intent journal. None of these require judgment in the moment —
that is the entire point of writing them now.

1. **Drawdown breach** — sleeve drawdown beyond the fortress's bootstrap 95th percentile.
2. **Friction breach** — realized friction exceeding the cost model by **>2 bps** over any
   20-trade window.
3. **Resync failure** — any startup/reconnect **resync** that cannot reconstruct broker state
   exactly (positions, open orders, bracket coverage).
4. **Orphaned bracket** — any **orphaned bracket** left after a corporate action (the
   cancel/rebuild guard around ex-dates failed).

## Standing rules

- Kill criteria are pre-set at G3 and may not be loosened once the pilot is live; tightening
  is allowed, loosening is a new plan version.
- A halted sleeve stays halted until the post-mortem identifies the cause and the fix passes
  the same gate that was failed.
- Every gate outcome — pass or fail — is recorded with its evidence (memo, fortress verdict
  table, chaos-test log, TCA dashboard extract) in the phase record.

## Timeline context

Total effort ~21–30 developer-days across 16 calendar weeks (part-time assumption). Critical
path: Item Zero → data layer → fortress → ops layer → G3. The execution lane may overlap the
data/fortress work if hours allow. Slack absorbs slow external confirmations
(`docs/external-confirmations.md`).
