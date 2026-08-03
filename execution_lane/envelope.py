"""Order envelopes and clamps (plan §5.3).

Server-side brackets at order origin are unchanged (broker.py); the envelope
is what wraps every intent before it may become an order. Three clamps, all
checked at boundary values in tests/test_envelope_clamps.py:

1. max_order_notional — a single order's qty*price may not exceed this.
2. max_trades_per_day — entries submitted in one session (overtrading guard).
3. sleeve_caps — per-sleeve aggregate notional (positions at cost basis plus
   live entry orders) may not exceed the sleeve's cap.

Semantics, pinned by the boundary tests:

- Clamps REDUCE an entry's quantity (whole shares, round down — the §8
  sizing rule) rather than rewriting its price; an intent clamped to zero
  shares is rejected, never silently placed.
- Exits (kind='exit') are never clamped or counted by the entry clamps: no
  risk limit may ever block or shrink an exit (same §8 principle as
  "rounding never blocks an exit", and the §9 kill criteria flatten per the
  envelope rules). An exit may still be rejected for malformed input.
- The notional test is exact at the boundary: notional == cap passes;
  cap + one cent clamps. Share counts use a 1e-9 floor epsilon so the
  boundary is exact in the pinned decimals, not hostage to IEEE reprs
  (cf. the 0.0035*100 note in tests/test_cost_model_boundaries.py).

`clamp()` is a pure function; the lane supplies the day's trade count and the
sleeve exposure derived from broker truth.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Mapping

_FLOOR_EPS = 1e-9


@dataclass(frozen=True)
class ClampConfig:
    max_order_notional: float
    max_trades_per_day: int
    sleeve_caps: Mapping[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class OrderIntent:
    symbol: str
    side: str                      # 'buy' | 'sell'
    qty: int
    limit_price: float
    sleeve: str
    kind: str = "entry"            # 'entry' | 'exit'
    reason: str = ""


@dataclass(frozen=True)
class ClampDecision:
    allowed: bool
    qty: int                       # quantity that may be placed (post-clamp)
    reasons: tuple                 # clamps that fired / why it was rejected
    clamped: bool                  # qty differs from the intent's


def _floor_shares(usd, price):
    """Whole shares purchasable for `usd` at `price`, rounding down. The
    1e-9 epsilon makes exact multiples land exactly (IEEE repr noise only;
    prices are cent-quantized, so real quotients never sit within 1e-9 of an
    integer unless they are meant to be one)."""
    if usd <= 0 or price <= 0:
        return 0
    return math.floor(usd / price + _FLOOR_EPS)


def clamp(intent: OrderIntent, config: ClampConfig, *, trades_today: int,
          sleeve_exposure: float) -> ClampDecision:
    """Evaluate one intent against the envelope. Pure: no I/O, no clock."""
    if intent.qty <= 0:
        return ClampDecision(False, 0, ("nonpositive_qty",), False)
    if intent.limit_price <= 0:
        return ClampDecision(False, 0, ("nonpositive_price",), False)
    if intent.kind == "exit":
        return ClampDecision(True, intent.qty, (), False)

    reasons = []
    if trades_today >= config.max_trades_per_day:
        return ClampDecision(False, 0, ("trades_per_day",), False)

    qty = intent.qty
    notional_cap = _floor_shares(config.max_order_notional, intent.limit_price)
    if qty > notional_cap:
        qty = notional_cap
        reasons.append("order_notional")

    cap = config.sleeve_caps.get(intent.sleeve)
    if cap is not None:
        sleeve_cap = _floor_shares(cap - sleeve_exposure, intent.limit_price)
        if qty > sleeve_cap:
            qty = sleeve_cap
            reasons.append("sleeve_cap")

    if qty <= 0:
        reasons.append("zeroed")
        return ClampDecision(False, 0, tuple(reasons), True)
    return ClampDecision(True, qty, tuple(reasons), qty != intent.qty)
