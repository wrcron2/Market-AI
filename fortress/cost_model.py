"""Cost model v1 — shared single source of truth (Trading System v2.4 plan,
§1.3 and §4.1).

Promoted from item_zero/cost_model.py: Item Zero, the validation fortress,
and later live TCA all import THIS module — if the live system ever measures
friction differently from the backtest, the comparison is meaningless.

Constants are pinned by the plan: $0.35 min/side commission, ~$0.0032/sh
pass-through, spread assumption by price bucket, whole-share rounding at
$640–1,070 sizing. Do not tune the constants here to make a backtest look
better; changes require a pre-registration amendment
(preregistration_template.md) filed before the affected run.

All functions are per side, per order. Entry and exit are both charged.
Boundary behavior is pinned by tests/test_cost_model_boundaries.py.
"""

COMMISSION_MIN_PER_SIDE = 0.35   # USD; the floor dominates at small order size
COMMISSION_PER_SHARE = 0.0035    # USD/share tiered rate under the floor
PASSTHROUGH_PER_SHARE = 0.0032   # USD/share exchange/regulatory pass-through

# Half-spread assumption per side, in bps of traded notional, by execution
# price bucket. M1's price band is $20–150, so buckets cover exactly that.
SPREAD_BPS_BUCKETS = (
    (20.0, 50.0, 5.0),
    (50.0, 100.0, 3.0),
    (100.0, 150.0, 2.0),
)


def spread_bps(price: float) -> float:
    """Half-spread cost per side, in bps of notional, for an execution price."""
    for lo, hi, bps in SPREAD_BPS_BUCKETS:
        if lo <= price < hi:
            return bps
    # Outside the M1 band: use the nearest bucket rather than failing loudly;
    # the band filter should make this unreachable in the M1 backtest.
    return SPREAD_BPS_BUCKETS[0][2] if price < 20.0 else SPREAD_BPS_BUCKETS[-1][2]


def commission(shares: int) -> float:
    """Commission + pass-through for one side of one order, in USD."""
    if shares <= 0:
        return 0.0
    return max(COMMISSION_MIN_PER_SIDE, COMMISSION_PER_SHARE * shares) + PASSTHROUGH_PER_SHARE * shares


def trade_cost(shares: int, price: float) -> float:
    """Total one-sided cost of a trade: commission + pass-through + half-spread."""
    if shares <= 0:
        return 0.0
    return commission(shares) + shares * price * spread_bps(price) / 1e4
