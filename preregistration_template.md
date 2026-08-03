# Pre-registration template — fortress runs (plan v2.4 §4.4)

**Fill this in BEFORE any fortress run whose output will be acted on.** The
expected CI width and the decision rule are computed and written down in
advance; the run either survives the width it was pre-registered under or it
does not. File the filled copy as `preregistrations/<date>_<sleeve>_<variant>.md`
(create the directory on first use). Amendments after the run starts are not
permitted; deviations discovered later are documented in the post-run section,
not patched here.

---

## 0. Header

- Run ID / date written / author:
- Sleeve & variant (must match `fortress/hurdles.yaml` naming):
- Gate this run feeds (G1–G5, or research-only):
- Fortress code version (commit or description) and cost-model version
  (`fortress/cost_model.py` constants hash or `sha1sum`):

## 1. Hypothesis

One sentence, falsifiable: which signal, on which universe, over which window,
is expected to clear which hurdle.

## 2. Frozen specification

- Signal rules (exact reference: file/section or frozen parameter list — no
  prose-only descriptions):
- Cadence / hold window (single value, or the bounded range being evaluated
  with both endpoints pre-declared):
- Universe source + provenance (§3 data layer series, as-of range,
  limitations accepted):
- Data window and embargo/fold structure:
- Sizing and cash-buffer policy (from the sleeve spec):
- Abort / inconclusive conditions (written now, e.g. insufficient universe
  load, too few eligible candidates on >25% of signal days):

## 3. Hurdle

Copied verbatim from `fortress/hurdles.yaml` for this sleeve — never invented
per-run:

- Rule(s) and threshold(s):
- CI machinery: circular block bootstrap, block 3, B=20,000, seed 42, 95%
  (any deviation justified here):

## 4. Expected CI width (compute BEFORE the run)

- Assumed per-period net-return volatility σ_p (state the source: prior
  fortress run, pilot data, or an assumption labeled as such):
- Expected number of periods n at the pinned cadence over the data window:
- Block length b (default 3 — holdings overlap adjacent periods):
- **Expected CI half-width ≈ 1.96 · σ_p · √(b / n)** (block bootstrap: the
  effective sample size is n/b, not n):
- The decision the run must support, restated against that width: "proceed
  only if the CI lower bound exceeds the hurdle" — write the exact inequality
  with the numbers above filled in:

## 5. Power statement — what this run can and cannot conclude

Fill both, using the anchors below (review recalculation, plan §4/§2.7):

- Anchors: detecting that a true ~10 bps/trade edge *exists* needs only
  ~100–400 trades; distinguishing 8 from 12 bps takes ~2,400 trades (5–10
  years at D1's rate). At rotation scale, n=24 monthly rotations answers "is
  there edge?" but not "how big is it?".
- This run CAN conclude:
- This run CANNOT conclude (and no decision will be made as if it could):

## 6. Decision rule (verbatim, binding)

> Proceed only if ____. Otherwise: ____ (park / extend paper / fix layer —
> name the fallback now, including which gate or layer owns the failure).

## 7. Post-run section (the only part written after)

- Actual CI width vs the §4 expectation (within stated tolerance?):
- Verdict per §6, unhedged:
- Deviations from this pre-registration, if any, and their direction of bias:
- Output artifacts (verdict table row, memo, results file):
