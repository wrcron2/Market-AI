# Item Zero — Go/Park Memo: M1 net-of-cost expectancy

**Date:** 2026-08-03 · **Window:** 2024-08-01 → 2026-07-31 (24 months) ·
**Rule:** `item_zero_decision_rule.md` (written before the run) ·
**Results:** `item_zero_results.csv`

**verdict: go**

Per the pre-written decision rule, at least one cadence must clear both bars —
(a) net annualized expectancy positive with the bootstrap confidence-interval
lower bound above zero, and (b) net expectancy exceeding 2× the modeled annual
friction drag. **Both** cadences cleared **both** bars, so the verdict is GO.
The build proceeds to Section 3 (point-in-time universe) with the fortress
reproduction of these numbers as the G1 gate.

## Numbers (as run, no edits)

| Cadence | Rotations | Trades | Net bps/rotation | Gross ann. % | Friction drag %/yr | **Net ann. %** | CI95 lower | CI95 upper | Rule A | Rule B |
|---|---|---|---|---|---|---|---|---|---|---|
| monthly | 24 | 110 | 591.5 | 71.71 | 0.72 | **70.98** | 32.31 | 103.99 | pass | pass |
| weekly | 105 | 232 | 114.6 | 61.10 | 1.49 | **59.61** | 1.21 | 119.73 | pass | pass |

Statistics: circular block bootstrap, block 3 rotations, 20,000 resamples, seed
42, 95% percentile confidence interval on annualized net expectancy.
Cost model v1 as pinned ($0.35 min/side + $0.0032/sh pass-through + 5/3/2 bps
half-spread by price bucket, whole-share rounding, $5,000 sleeve, $1,000/position).

## What the numbers say

- **Monthly cadence is the stronger candidate** and is the one to pin: higher
  net expectancy (70.98% vs 59.61%/yr), lower friction (0.72% vs 1.49%/yr), and
  a confidence-interval lower bound far above zero (32.31%/yr).
- **Weekly also passes, but its confidence lower bound (1.21%/yr) barely clears
  zero** — at 105 rotations the series is much choppier (46 of 105 weeks
  negative). Weekly is not the pinned cadence unless the fortress reverses this.
- Modeled friction (0.72%/1.49%/yr) came in **below** the review's pre-estimate
  bands (0.8–1.3% / 3.4–5.7%/yr) because actual membership-change turnover
  (~93%/~43% of sleeve per monthly/weekly rotation, both-sides) is well under
  the full-turnover assumption behind those bands. Rule B passes regardless.
- Worst periods: −18.9% net (Jul 2026, monthly) and −20.0% net (single week).
  Monthly CI95 width is ±~36 pts around the mean — wide, as expected at n=24.

## Limitations that qualify — but do not change — the verdict

1. **Survivorship bias (the big one).** The universe is today's S&P 100, a
   documented free proxy per plan §1.1. A momentum filter over a survivors-only
   universe in a strong bull window is biased **upward**, and this window
   (2024-08→2026-07) was an exceptionally strong momentum tape. The honest
   reading: Item Zero says M1's *mechanics* clear the cost hurdle with margin;
   the *level* of expectancy must not be trusted until the fortress re-runs it
   on the Section 3 point-in-time universe (delistings retained). G1 exists to
   catch exactly this. Expect the reproduction number to be lower.
2. **Signal reconstruction (hidden dependency).** Plan v2.3, which §1.2 names
   as the source of M1's exact signal rules, is not in this repository and was
   not attached to the task. M1 was reconstructed from the v2.4 plan's own
   pinned parameters (90-trading-day momentum, top 5, $20–150 band, equal
   weight, no overlays) and frozen in the decision rule before the run. If
   v2.3's actual rules differ, this test must be re-run with them.
3. **Statistical power.** n=24 monthly rotations can answer "is there edge?"
   but not "how big is it?" (plan §2.7). The weekly cadence's marginal rule-A
   pass illustrates the CI width problem the review flagged.
4. Minor: free Yahoo data (adjusted series, 99/101 tickers loaded — GEV and
   HONA dropped for insufficient history per the pre-registered rule);
   exit commissions charged on entry share counts (error ≪ $0.35 floor);
   idle cash earns 0%.

## Provenance

Decision rule written before the run (`item_zero_decision_rule.md`, 2026-08-03,
before any data download). Code: `item_zero/` (frozen universe list, cost model,
fetcher, runner). Raw data cached at `item_zero/data/prices.csv`; run detail at
`item_zero/data/run_report.json`. Reproduce: `cd item_zero && python3
fetch_data.py && python3 run_expectancy.py`.

**Next action per the plan:** Section 2 (v2.4 doc corrections) and Section 3
(point-in-time universe) may proceed. G1 will hold the fortress to reproducing
these numbers on survivorship-free data before any execution-lane work.

## G1 update (2026-08-03, later the same day): fortress reproduction

The validation fortress (`fortress/`, plan §4) re-ran both cadences on this
memo's price cache through the shared cost model and reproduced every number
above within the CSV's own rounding — the cross-check is pinned by
`tests/test_fortress_reproduces_item_zero.py` (G1 gate, machine-checkable).
Hurdles are pre-registered in `fortress/hurdles.yaml` and printed at the top
of every fortress report; the standardized verdict table is
`fortress_verdict_table.csv` (monthly: pass, weekly: pass). Per the §8 pin
rule, **monthly is the pinned M1 cadence** (sleeve_specs/m1.md).

Two qualifications stand unchanged. (1) This reproduction ran on the same
survivorship-biased proxy universe — the survivorship-free re-run still awaits
price history for departed names; the §3 data layer carries membership,
symbol_map, and corporate actions but no price series yet (hidden dependency,
carried to the data-layer backlog). (2) The cost model was promoted to
`fortress/cost_model.py` as the single source of truth (§4.1); Item Zero's
runner now imports it, and re-running Item Zero after the promotion produced
a byte-identical `item_zero_results.csv`.
