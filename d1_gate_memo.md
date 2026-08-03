# D1 Gate Memo — G5 decision

**Date:** 2026-08-03 · **Gate:** G5 (plan v2.4 §7, §9) ·
**Sleeve spec:** `sleeve_specs/d1.md` · **Hurdles:** `fortress/hurdles.yaml`

**Verdict: Option B — park.** Keep the code path, allocate no capital,
revisit if the account grows 3–5×.

## The hurdle, restated beside the verdict

- **As specified (parked):** D1 must show **gross per-trade expectancy
  above ~10 bps** — `d1.rules.gross_per_trade_bps_gt: 10.0` in
  `fortress/hurdles.yaml` (plan §4.2, pre-registered).
- **If ever re-cut (Option A):** the hurdle is restated to **~6 bps
  gross** for the ≥$80 liquid-names universe at ~50 high-conviction
  trades/yr (plan §7 option A / §8; the ≥$80 band floor is pinned as
  `price_band_usd_if_recut` in the same file).

Any future D1 paper run is measured against the ~10 bps hurdle as pinned
today, or against the restated ~6 bps hurdle if — and only if — the
universe is re-cut to ≥$80 names first. The hurdle is not movable after a
run starts (pre-registration amendment rule, `preregistration_template.md`).

## Why B — the evidence in hand

1. **The friction arithmetic stands unrebutted (plan §7).** At 1–2
   trades/day on a $1,500 sleeve, friction is 14–51% of the sleeve per
   year; below ~$45/share friction alone exceeds the entire stylized ORB
   edge (8.5 bps). Parking costs nothing; trading costs the friction.
2. **G5's written default applies.** The plan's gate table (§9) states:
   "Default is B (park) if evidence is ambiguous." There is **no** D1
   fortress evidence — the fortress has no intraday data layer, and no
   D1 run exists or was planned before the §3 data layer matures. Absent
   evidence is the ambiguous case the default was written for.
3. **Item Zero's GO verdict covers M1 only** (`item_zero_memo.md`);
   nothing in it rehabilitates D1's economics.
4. **IBKR Rule 4210 status (open question Q2) is unanswered** — intraday
   margin reliance, which an ORB sleeve assumes, is unconfirmed for the
   account. D1's feasibility resting on a ticket answer was the plan's
   own framing; the ticket answer is not in hand.

## Options A and C — considered, rejected for now

- **Option A (re-cut: ≥$80 names, ~50 trades/yr, restated ~6 bps gross
  hurdle):** rejected today because it would require a fortress run on an
  intraday data layer that does not exist, and its own power analysis
  (plan §7) says statistical confirmation would take years even if the
  run were clean. It remains the only revival path; triggers below.
- **Option C (drop, reallocate the $1,500 to M1/M2):** rejected because
  parking is free and deletion is irreversible. M1/M2 cleared their
  gates and carry the capital; D1's code path stays parked at zero
  allocation, so there is nothing to gain by dropping it before the
  account-size math is revisited.

## Revival triggers (what reopens Option A)

Per plan §7 option B and `sleeve_specs/d1.md`, all of:

1. Account size grows 3–5× (the $0.35 commission floor shrinks relative
   to position size; friction in bps is scale-invariant, the floor is
   not).
2. IBKR Rule 4210 answer (Q2) confirms the account's intraday margin
   status.
3. A fortress run on the ≥$80 universe clears the restated ~6 bps gross
   hurdle — pre-registered before the run.

And if any variant ever proceeds, the §7 protocol is non-negotiable:
minimum **100 paper trades** at the intended cadence; realized friction
tracked against the shared cost model (`ops/tca_friction.py`, alert at
>2 bps excess over any 20-trade window); go-live only if gross expectancy
clears the written hurdle **and** realized friction lands inside model
bands; pre-registration stating in advance that a 100-trade paper run
**cannot confirm** a ~10 bps edge — it can only fail to reject it; any
live start at toy size, buying information, not returns.

## Provenance

Decision rule source: plan v2.4 §7 (three options) and §9 gate G5
(default B on ambiguous evidence). Hurdles: `fortress/hurdles.yaml`
(pinned 2026-08-03). Sleeve record: `sleeve_specs/d1.md` (status:
parked). This memo changes no code path and allocates no capital.
