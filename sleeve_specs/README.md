# Sleeve specifications — pinned parameters (Trading System v2.4 plan, §8)

Every cost-relevant parameter the review flagged is pinned here: a single
value, or an explicit bounded range with its cost consequence stated. Nothing
in this directory is left open. Pins were made 2026-08-03 on the basis of the
Item Zero run (`item_zero_results.csv`) and its fortress reproduction
(`fortress_verdict_table.csv`); the evidence chain for each value is cited in
the sleeve's own file.

| Parameter | Pinned value | Cost consequence | Evidence |
|---|---|---|---|
| M1 rotation cadence | **monthly** | drag 0.72%/yr vs 1.49%/yr weekly; CI95 lb 32.31%/yr vs 1.21%/yr | `fortress_verdict_table.csv`, `item_zero_memo.md` |
| M2 hold window | **[3, 10] trading days** (bounded range; both endpoints fortress-evaluated at the M2 gate, pin rule below) | worst-case full-turnover friction 9.2–17.0%/yr at 3-day vs 2.8–5.1%/yr at 10-day | `m2.md` computation via shared cost model |
| Price band per sleeve | M1/M2: **$20–150**; D1 (if re-cut): **≥$80** | below $20 the penny-spread asymmetry dominates; $150 cap keeps whole-share granularity acceptable | plan §7/§8; `item_zero_memo.md` |
| Sizing / rounding | **whole shares, round down**; cash buffer **≥ 1 share of the most expensive in-band candidate** ($150 for M1/M2) | rounding strands 5–20% of a sleeve in cash at $60–150 names; carried in the expectancy model as 0%-yield residual | plan §8; `tests/test_cost_model_boundaries.py` |

Sleeves: [`m1.md`](m1.md) · [`m2.md`](m2.md) · [`d1.md`](d1.md)

Hurdles live in `fortress/hurdles.yaml` (pre-registered, plan §4.2) and are
printed at the top of every fortress report. Any parameter change here is a
new experiment: file `preregistration_template.md` before the run that uses
it.
