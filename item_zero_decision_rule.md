# Item Zero — Decision Rule (written BEFORE the run)

**Written:** 2026-08-03, before any market data was downloaded or any expectancy
number computed. Environment reconnaissance only (Python packages, data-source
reachability, constituent-list retrieval) preceded this file. The results file
`item_zero_results.csv` did not exist when this rule was committed to disk.

**Scope:** Trading System v2.4 implementation plan, Section 1 ("Item Zero: M1
net-of-cost expectancy test"). This file is the pre-registered decision rule
required by the plan ("Decision rule, written in advance"). Nothing below may be
edited after the run; any deviation discovered later is documented in the memo,
not patched here.

---

## 1. The decision rule (verbatim from the plan, then operationalized)

Plan §1: *"Proceed with the build only if both hold for at least one cadence:
(a) net annualized expectancy is positive with the bootstrap confidence-interval
lower bound above zero; (b) net expectancy exceeds 2× the modeled annual friction
drag, giving margin for model error. If neither cadence clears both bars, the
correct outcome of this section is to park the trading system."*

Operationalized:

- For each cadence c ∈ {monthly, weekly}:
  - **Rule A (confidence):** lower bound of the 95% bootstrap confidence
    interval of annualized net expectancy > 0.
  - **Rule B (margin):** annualized net expectancy > 2 × annualized friction
    drag (equivalently: gross edge > 3 × friction).
- **Verdict = GO** if at least one cadence passes both rules.
- **Verdict = PARK** otherwise. PARK is a success condition for Item Zero, not a
  failure — it costs a weekend instead of sixteen weeks.
- The verdict is stated without hedging in `item_zero_memo.md` as
  `verdict: go` or `verdict: park`.

## 2. Frozen test specification

**Window.** Test window 2024-08-01 → 2026-07-31 inclusive (24 months, the plan's
"12–24 months of history" upper bound; window ends at the last completed month
before this run). Price history is fetched from 2023-11-01 to supply the signal
lookback.

**Universe (documented free proxy, plan §1.1 fallback — no paid vendor
available).** Current S&P 100 constituents (101 tickers), retrieved from
Wikipedia on 2026-08-03, frozen here:

AAPL, ABBV, ABT, ACN, ADBE, AMAT, AMD, AMGN, AMT, AMZN, AVGO, AXP, BA, BAC,
BKNG, BLK, BMY, BNY, BRK-B, C, CAT, CL, CMCSA, COF, COP, COST, CRM, CSCO, CVS,
CVX, DE, DHR, DIS, DUK, EMR, FDX, GD, GE, GEV, GILD, GM, GOOG, GOOGL, GS, HD,
HONA, IBM, INTC, INTU, ISRG, JNJ, JPM, KO, LIN, LLY, LMT, LOW, LRCX, MA, MCD,
MDLZ, MDT, META, MMM, MO, MRK, MS, MSFT, MU, NEE, NFLX, NKE, NOW, NVDA, ORCL,
PEP, PFE, PG, PLTR, PM, QCOM, RTX, SBUX, SCHW, SO, SPG, T, TMO, TMUS, TSLA,
TXN, UBER, UNH, UNP, UPS, USB, V, VZ, WFC, WMT, XOM

Stated limitations (accepted per plan §1.1): (i) survivorship bias — these are
today's large caps; delisted/bankrupt names of 2024–2026 are absent, biasing
results **upward** (works against a PARK verdict, noted in the memo);
(ii) membership is static, not point-in-time; (iii) any ticker that fails to
download is dropped and logged. If fewer than 80 of the 101 tickers load, the
run aborts as inconclusive. If the verdict is GO, Section 3 of the plan rebuilds
this universe properly (point-in-time, delistings retained) and the fortress
must reproduce Item Zero's numbers.

**Signal (reconstructed — see deviation note).** Plan v2.3, which §1.2 says
holds M1's exact signal rules, is not present in this repository and was not
attached to the task; this is reported as a hidden dependency. M1 is therefore
reconstructed from the v2.4 plan's own pinned parameters (§1, §8): a
cross-sectional momentum rotation over the band-filtered universe. Frozen rule:

- Momentum score = 90-trading-day total return from split-and-dividend-adjusted
  closes: `AdjClose(t) / AdjClose(t − 90 trading days) − 1`.
- Candidate filter on the signal day: raw close in the plan's M1 price band
  $20–$150, momentum computable.
- Portfolio: top 5 candidates by momentum, equal target weight.
- No stop-loss, no absolute-momentum overlay, no parameter tuning — one fixed
  specification, no sweeps.

**Cadences.** Both are run; the answer "does M1 have edge" cannot be separated
from cadence (plan §1).

- Monthly: rebalance on the first trading day of each calendar month.
- Weekly: rebalance on the first trading day of each ISO week.

Signal day = last trading day before the rebalance day (no look-ahead).
Execution at the rebalance day's raw open.

**Sizing (plan §8).** Sleeve notional fixed at $5,000 (no compounding — all
figures are arithmetic, as % of sleeve value, matching the review's friction
framing). Target $1,000 per position; whole shares, round down; residual cash
earns 0%. This lands every position inside the plan's $640–$1,070 sizing band
for any $20–$150 entry price.

**Trading rule.** Membership changes only: at each rebalance, sell names that
left the top 5, buy names that entered; persistent names are untouched (no
drift rebalancing — the realistic implementation for a small sleeve, and the
cost-minimizing one). Positions still open at the window end are liquidated at
the final close and charged full exit costs. Costs are attributed to the
rotation period they open.

**Cost model v1 (plan §1.3 constants, literal).** Per side, per order:

- Commission: `max($0.35, $0.0035 × shares)` — the $0.35 minimum is the whole
  story at this size.
- Pass-through fees: `$0.0032 × shares`.
- Half-spread by execution-price bucket (of traded notional): $20–50 → 5.0 bps;
  $50–100 → 3.0 bps; $100–150 → 2.0 bps.
- Entry and exit both charged. Sanity anchor: at full turnover this model
  reproduces the review's friction bands (≈0.8–1.3%/yr monthly,
  ≈3.4–5.7%/yr weekly); actual modeled turnover is what it is.

**Data handling.** Yahoo Finance daily bars (retrieval date 2026-08-03), raw +
adjusted. Signals and returns use adjusted prices; share counts, price-band
checks, and notionals use raw prices (the plan §3 adjustment boundary, applied
even in this research-grade pass). Tickers with >5% missing bars over the fetch
span are dropped and logged; smaller gaps are forward-filled (adjusted series
only) and counted.

## 3. Metrics and statistics (frozen)

Per cadence: number of rotations; number of trades; mean eligible-candidate
count; mean turnover per rotation; per-rotation gross and net bps of sleeve;
annualized gross %; annualized friction drag %; annualized net %
(= gross − drag, arithmetic, ×12 monthly / ×52 weekly).

Confidence interval: circular block bootstrap over the per-rotation net return
series, block length 3 rotations (momentum holdings overlap between consecutive
rotations, so plain iid resampling understates the width), 20,000 resamples,
seed 42, percentile method, 95% level — the "bootstrap confidence interval" of
Rule A.

## 4. Abort / inconclusive conditions (frozen)

- <80 of 101 tickers load → run invalid, report inconclusive.
- Fewer than 5 eligible candidates on >25% of a cadence's signal days → that
  cadence reported as untestable (not silently passed or failed).
- Otherwise the numbers stand as computed and the verdict follows §1 exactly.
