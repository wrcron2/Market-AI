---
description: Risk management rules, position sizing formulas, portfolio constraints, and market regime rules for the MarketFlow AI risk agent and Green Light gate
---

# Risk and Portfolio Management

This skill covers the financial domain rules behind the risk agent (`risk_agent.py`), the Green Light gate, and portfolio-level constraints. It is the authoritative reference for setting env vars, writing risk agent prompts, and deciding when to block or scale a signal.

---

## Risk Agent Output Fields — What They Mean

```python
class _RiskOutput(BaseModel):
    is_blocked: bool              # True = signal dropped entirely
    block_reason: str             # Why it was blocked (shown in dashboard)
    risk_score: float             # 0.0 (safe) → 1.0 (dangerous)
    risk_notes: str               # Human-readable 2-3 sentence summary
    confidence_adjustment: float  # −0.20 to +0.05 applied to debate confidence
    quantity_multiplier: float    # 0.10 to 1.0 applied to original quantity
```

The risk agent is the **last automated gate** before the Green Light queue. Its job is not to find good trades — that is the signal and debate agents' job. Its job is to catch trades that are too risky regardless of signal quality.

---

## When to Block (is_blocked = true)

Block the signal outright (do not pass to Green Light queue) in any of these cases:

| Condition | Block reason text |
|---|---|
| final_confidence < MIN_SIGNAL_CONFIDENCE (0.90) | "Post-risk confidence X% < threshold 90%" |
| VIX > 40 and direction = BUY | "Extreme fear (VIX > 40) — BUY signals suspended" |
| ATR% > 8% and confidence < 0.93 | "Extreme volatility (ATR > 8%) — insufficient conviction" |
| volume < 0.3 × volume_sma20 | "Illiquid — volume < 30% of average" |
| risk_score ≥ 0.80 | "Risk score too high regardless of confidence" |
| Parse error in risk agent | "Risk agent parse error — blocked conservatively" |

Never unblock a parse-error case. Fail safe = block.

---

## Risk Score Reference (risk_score: 0.0–1.0)

| risk_score | Meaning | Typical action |
|---|---|---|
| 0.0–0.25 | Low risk — textbook setup | Full quantity, no confidence penalty |
| 0.25–0.50 | Moderate risk — some concerns | quantity_multiplier 0.75–1.0, small confidence adj |
| 0.50–0.70 | Elevated risk — notable headwinds | quantity_multiplier 0.50–0.75, confidence −0.05 to −0.10 |
| 0.70–0.80 | High risk — major concerns | quantity_multiplier 0.25–0.50, confidence −0.10 to −0.20 |
| 0.80–1.0 | Extreme risk | Block (is_blocked = true) |

---

## Position Sizing Rules (quantity_multiplier)

The risk agent scales the signal agent's proposed `quantity` via `quantity_multiplier` (0.10–1.0). The result is also hard-capped at `RISK_MAX_QUANTITY` (default 10,000 shares).

### Multiplier by VIX regime

| VIX | quantity_multiplier |
|---|---|
| < 15 | 1.0 (full size) |
| 15–20 | 0.90 |
| 20–25 | 0.75 |
| 25–30 | 0.50 |
| 30–40 | 0.25 |
| > 40 | 0.0 (block) for BUY; 0.25 for SHORT |

### Multiplier by ATR volatility

```python
atr_pct = atr_14 / close * 100
```

| atr_pct | quantity_multiplier modifier |
|---|---|
| < 2% | × 1.0 (no change) |
| 2–3% | × 0.90 |
| 3–5% | × 0.75 |
| 5–8% | × 0.50 |
| > 8% | × 0.25 (or block) |

Apply both multipliers multiplicatively:
```python
final_multiplier = vix_multiplier * atr_multiplier
# e.g. VIX=27 (0.50) × ATR%=4% (0.75) = 0.375
```

### Dollar-value sanity check

The risk agent prompt should include a rough dollar exposure check. For a $100k virtual portfolio:
- Max single position = 10% of portfolio = $10,000
- At a $50 stock, that is 200 shares max
- At a $500 stock, that is 20 shares max

If the signal agent proposes 1,000 shares of a $200 stock ($200,000 exposure), the risk agent must scale quantity down to keep exposure ≤ $10,000, regardless of confidence.

```
max_shares = 10_000 / close_price   # 10% of $100k portfolio
adjusted_quantity = min(proposed_quantity, max_shares) * final_multiplier
```

---

## Confidence Adjustment Rules (confidence_adjustment: −0.20 to +0.05)

Apply negative adjustments for risk factors; small positive for exceptional setups.

| Factor | Adjustment |
|---|---|
| VIX > 30 | −0.10 |
| VIX > 25 | −0.05 |
| ATR% > 5% | −0.05 |
| ATR% > 8% | −0.10 |
| volume < 0.5 × sma20 | −0.05 |
| BUY signal in spy_trend = "downtrend" | −0.05 |
| SHORT signal in spy_trend = "uptrend" | −0.05 |
| Market order (limit_price = 0) on volatile stock (ATR% > 4%) | −0.03 |
| All indicators aligned + high volume + macro tailwind | +0.03 to +0.05 |

Maximum positive adjustment is +0.05 (the risk agent should not inflate confidence significantly).
Maximum negative adjustment is −0.20 per the risk agent output schema.

---

## Market Regime Rules

These combine VIX + spy_trend into a single regime classification for decision-making.

```
VIX < 15  +  spy_trend = uptrend   →  BULL MARKET
VIX < 20  +  spy_trend = sideways  →  RANGE MARKET
VIX > 20  +  spy_trend = downtrend →  BEAR MARKET
VIX > 30  (any trend)              →  CRISIS MODE
```

### How each regime changes behaviour

**BULL MARKET**
- BUY signals: standard thresholds apply
- SHORT signals: require RSI > 72 + MACD bearish crossover + bear argument wins debate decisively
- quantity_multiplier: 1.0
- confidence floor: 0.90 (default)

**RANGE MARKET**
- Both BUY and SELL acceptable but require multi-indicator agreement
- quantity_multiplier: 0.75–0.90
- confidence floor: 0.90 (default)

**BEAR MARKET**
- SHORT / SELL signals: standard thresholds apply
- BUY signals: require RSI < 28 + volume spike (> 2× sma20) + bull wins debate decisively
- quantity_multiplier: 0.50
- confidence floor: raise to 0.92 (tighten)

**CRISIS MODE (VIX > 30)**
- Block all new BUY signals
- SHORT signals allowed at reduced size (quantity_multiplier 0.25)
- COVER signals always allowed (closing existing shorts is always safe)
- confidence floor: raise to 0.95

---

## Environment Variables — Risk Thresholds

| Env var | Default | Meaning |
|---|---|---|
| `MIN_SIGNAL_CONFIDENCE` | `0.90` | Hard floor — signals below this are blocked after risk adjustment |
| `RISK_MAX_QUANTITY` | `10000` | Absolute max shares per signal regardless of multiplier |

When to change `MIN_SIGNAL_CONFIDENCE`:
- Raise to `0.93–0.95` in BEAR MARKET or CRISIS MODE
- Lower to `0.87–0.88` only during backtesting / high signal volume experiments
- Never go below `0.85` in live IBKR mode

---

## Portfolio-Level Constraints

The virtual portfolio starts at $100,000. These constraints apply at the portfolio level (not per-signal):

| Rule | Limit |
|---|---|
| Max single position exposure | 10% of portfolio ($10,000) |
| Max sector concentration | 30% of portfolio in one sector |
| Max correlated positions (e.g. 2 semiconductor stocks) | 2 simultaneous |
| Max open positions | 10 (diversification) |
| Max drawdown trigger (suspend new BUY signals) | −15% portfolio value |

These are not enforced in code yet — they are the intended guardrails for when the portfolio tracking module is extended. When implementing portfolio checks, read current open positions from the Go backend's DB before staging a new signal.

---

## Green Light Gate — What the Human Sees

When a signal reaches the Green Light queue, the risk assessment is already complete. The human reviewer should focus on:

1. **Confidence bar** — green ≥ 95%, yellow ≥ 90%, red < 90% (should not appear if floor is enforced)
2. **risk_score** — displayed as context; high risk_score (> 0.6) with borderline confidence warrants rejection
3. **risk_notes** — the 2-3 sentence human-readable summary from the risk agent
4. **adjusted_quantity** — check this makes sense relative to the stock's price
5. **Market context** — if VIX spiked since the signal was generated, reject manually

The Green Light is the final safety net. When in doubt, reject. The AI generates signals continuously; missing one signal is never a problem.

---

## Prompt Engineering — Risk Agent System Prompt Additions

When updating `RISK_SYSTEM` in `risk_agent.py`, include these financial domain instructions:

```
- High VIX (> 25) = elevated market fear; reduce size and confidence
- ATR% > 5% = high daily volatility; reduce quantity_multiplier accordingly
- Market orders (limit_price = 0) on volatile stocks carry execution risk; penalise slightly
- Volume below 50% of 20-day average = low conviction; reduce confidence
- Counter-trend signals (BUY in downtrend, SHORT in uptrend) need extra conviction to pass
- Never output risk_score < 0.1 — there is always some risk
- When blocking, write a block_reason that a non-technical trader can understand
```
