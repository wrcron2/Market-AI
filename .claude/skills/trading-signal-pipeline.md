---
description: Conventions for the three-stage AI trading signal pipeline (Generate → Debate → Risk), including indicator interpretation and signal confidence calibration
---

# Trading Signal Pipeline Conventions

## Signal Model

Signals are immutable Pydantic models. Always validate `direction` and `confidence` with `@field_validator`.

```python
class CandidateSignal(BaseModel):
    signal_id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    symbol: str
    direction: str        # BUY | SELL | SHORT | COVER
    quantity: float
    limit_price: float = 0.0   # 0 = market order
    reasoning: str
    strategy_name: str
    initial_confidence: float  # 0.0–1.0

    @field_validator("direction")
    @classmethod
    def validate_direction(cls, v: str) -> str:
        allowed = {"BUY", "SELL", "SHORT", "COVER"}
        if v.upper() not in allowed:
            raise ValueError(f"direction must be one of {allowed}")
        return v.upper()

    @field_validator("initial_confidence")
    @classmethod
    def validate_confidence(cls, v: float) -> float:
        if not 0.0 <= v <= 1.0:
            raise ValueError("initial_confidence must be 0.0–1.0")
        return v
```

## Market Snapshot Structure

The canonical snapshot dict passed between data feed and agents:

```python
{
    "symbol": "AAPL",
    "ohlcv": {
        "open": 150.25, "high": 152.50, "low": 149.80,
        "close": 151.25, "volume": 45_000_000,
    },
    "indicators": {
        "rsi_14": 65.2, "macd": 0.1234, "macd_signal": 0.1100,
        "bb_upper": 155.50, "bb_lower": 147.80, "atr_14": 1.50,
        "volume_sma20": 42_000_000, "sma_20": 150.50, "sma_50": 148.75,
    },
    "market_context": {
        "vix": 18.5,
        "spy_trend": "uptrend",    # uptrend | downtrend | sideways
        "sector_flow": "risk-on",  # risk-on | risk-off | neutral
    },
    "_source": "yfinance",
    "_timestamp": 1715000000000,
}
```

- Underscore-prefixed keys (`_source`, `_timestamp`) are metadata, not trading inputs
- Indicators computed once at fetch time, not inside agents
- Macro context (VIX, SPY trend) fetched once per bar and shared across all symbols

## Stage 1 — Generate (Ollama / LOW complexity)

Goal: produce a `CandidateSignal | None` from a market snapshot.

```python
def generate(self, market_snapshot: dict[str, Any]) -> CandidateSignal | None:
    raw = self.router.complete(
        system=self.SYSTEM_PROMPT,
        user=f"Analyze:\n{json.dumps(market_snapshot, indent=2)}\nOutput JSON only.",
        complexity=Complexity.LOW,
        schema=CandidateSignal,
    )
    try:
        match = re.search(r'\{.*\}', raw, re.DOTALL)
        data = json.loads(match.group(0) if match else raw)
        data.setdefault("strategy_name", self.strategy_name)
        return CandidateSignal(**data)
    except (json.JSONDecodeError, ValueError) as exc:
        log.error("signal_agent.parse_error", error=str(exc), raw=raw[:200])
        return None   # failure returns None; caller handles gracefully
```

Rules:
- System prompt instructs model to output JSON only, no markdown
- Regex fallback extracts JSON if model adds surrounding text
- Parse failure → return `None`, not an exception
- Log raw output (truncated) on parse errors

## Stage 2 — Debate (three LLM calls, LOW complexity)

Goal: produce a `DebateResult` with an `adjusted_confidence` and `consensus_direction`.

```python
bull_arg = router.complete(system=BULL_SYSTEM, user=f"Argue FOR:\n{signal_summary}", complexity=Complexity.LOW)
bear_arg = router.complete(system=BEAR_SYSTEM, user=f"Argue AGAINST:\n{signal_summary}", complexity=Complexity.LOW)
judge_raw = router.complete(
    system=JUDGE_SYSTEM,
    user=f"Trade:\n{signal_summary}\nBull:\n{bull_arg}\nBear:\n{bear_arg}\nSynthesize.",
    complexity=Complexity.LOW,
    schema=DebateResult,
)
```

Graceful degradation on parse error — penalise confidence but don't block:

```python
except (json.JSONDecodeError, KeyError, ValueError):
    return DebateResult(
        bull_argument=bull_arg,
        bear_argument=bear_arg,
        judge_reasoning="Judge parse error — confidence penalised.",
        adjusted_confidence=max(0.0, signal.initial_confidence - 0.15),
        consensus_direction=signal.direction,   # keep original direction
    )
```

Log: original vs adjusted confidence, and whether direction changed.

## Stage 3 — Risk (Ollama / LOW complexity)

Goal: produce a `RiskAssessment` that may block the signal, adjust confidence, or scale quantity.

```python
class _RiskOutput(BaseModel):
    is_blocked: bool
    block_reason: str = ""
    risk_score: float          # 0.0–1.0
    risk_notes: str
    confidence_adjustment: float = 0.0
    quantity_multiplier: float = 1.0

# Hard confidence floor enforced after adjustments
final_conf = max(0.0, min(1.0, debate.adjusted_confidence + data["confidence_adjustment"]))
qty = min(self.max_quantity, signal.quantity * data["quantity_multiplier"])

if not data["is_blocked"] and final_conf < self.min_confidence:
    is_blocked = True
    block_reason = f"Post-risk confidence {final_conf:.0%} < threshold {self.min_confidence:.0%}"
```

Graceful degradation on parse error — block conservatively:

```python
except (json.JSONDecodeError, ValueError):
    return RiskAssessment(is_blocked=True, block_reason="Risk agent parse error", ...)
```

## Data Feed — Batched Fetching

```python
# Macro context fetched once per bar (shared across symbols)
vix_val   = self._fetch_last_close("^VIX")
spy_trend = self._classify_trend("SPY")

# Split universe into chunks to avoid throttling
chunks = [symbols[i:i+300] for i in range(0, len(symbols), 300)]
for chunk in chunks:
    chunk_data = self._download_chunk(chunk, period)
    for symbol in chunk:
        try:
            snapshot = self._build_snapshot(symbol, chunk_data, vix_val, spy_trend)
            if snapshot:
                snapshots.append(snapshot)
        except Exception as exc:
            log.warning("yahoo_feed.symbol_error", symbol=symbol, error=str(exc))
```

Rules:
- Download in chunks of 300 symbols maximum
- Per-symbol errors are caught and logged but never fail the batch
- Return list of snapshots; downstream filters empty ones

## Main Loop — Parallel Symbol Processing

```python
with ThreadPoolExecutor(max_workers=PIPELINE_WORKERS) as pool:
    futures = {pool.submit(_process, s): s["symbol"] for s in snapshots}
    for future in as_completed(futures):
        if exc := future.exception():
            log.error("brain.worker_error", symbol=futures[future], error=str(exc))
```

- Default 5 workers; configurable via env var
- Worker exceptions logged but don't crash the loop
- Orchestrator result checked for `submitted` flag before calling executor
- Mode (yahoo/ibkr) checked once at bar start and used for the entire bar

---

## Indicator Interpretation Guide

These are the exact indicators computed by `yahoo_feed.py` and passed to the signal agent. Use these rules when writing or reviewing agent prompts, evaluating signal quality, or calibrating confidence scores.

### RSI (rsi_14) — Momentum / Overbought-Oversold

| Value | Meaning | Signal implication |
|---|---|---|
| < 30 | Oversold | BUY bias — potential mean reversion up |
| 30–45 | Recovering | Weak BUY bias — confirm with MACD |
| 45–55 | Neutral | No directional edge — avoid low-conviction signals |
| 55–70 | Trending up | BUY momentum — strong if volume confirms |
| > 70 | Overbought | SELL / SHORT bias — fade the move or wait for reversal |

Rules:
- RSI alone is not a signal — it must confirm with at least one other indicator
- RSI divergence (price makes new high, RSI does not) is a reversal warning; penalise `initial_confidence` by 0.05–0.10
- In strong uptrends (spy_trend = "uptrend"), overbought readings can persist — raise the SELL threshold to 75+

### MACD / MACD Signal — Trend and Momentum Crossovers

- `macd` = 12-EMA minus 26-EMA (the MACD line)
- `macd_signal` = 9-EMA of MACD (the signal line)

| Condition | Meaning |
|---|---|
| `macd > macd_signal` and both positive | Strong uptrend — BUY confirmation |
| `macd > macd_signal` but both negative | Recovery from downtrend — weak BUY |
| `macd < macd_signal` and both negative | Strong downtrend — SELL / SHORT confirmation |
| `macd < macd_signal` but both positive | Momentum fading — watch for reversal |
| `macd` crosses above `macd_signal` | Bullish crossover — BUY signal trigger |
| `macd` crosses below `macd_signal` | Bearish crossover — SELL signal trigger |

Rules:
- A crossover in the direction of `spy_trend` is stronger than a counter-trend crossover
- MACD crossovers near zero line are more reliable than crossovers far from zero

### Bollinger Bands (bb_upper / bb_lower) — Volatility and Price Extremes

```
Price near bb_upper  →  price at top of normal range → overbought / SELL bias
Price near bb_lower  →  price at bottom of range     → oversold / BUY bias
Price outside bands  →  breakout or extreme move
```

How to compute band position from snapshot:
```python
# BB %B: where is close within the band? 0 = at lower, 1 = at upper
bb_pct_b = (close - bb_lower) / (bb_upper - bb_lower)
```

| bb_pct_b | Meaning |
|---|---|
| < 0.0 | Below lower band — extreme oversold, potential reversal BUY |
| 0.0–0.2 | Near lower band — oversold zone |
| 0.4–0.6 | Mid-band — no edge |
| 0.8–1.0 | Near upper band — overbought zone |
| > 1.0 | Above upper band — breakout or blow-off top |

Rules:
- Bollinger Band squeeze (upper − lower is narrow) signals a volatility expansion is coming — direction unknown, hold off or reduce quantity
- Breakouts above `bb_upper` with volume > `volume_sma20` are genuine breakouts; below `bb_lower` with high volume are breakdowns

### ATR (atr_14) — Volatility / Position Sizing

ATR = Average True Range over 14 days = typical daily price swing in dollars.

Uses in this app:
1. **Stop distance proxy** — a rational stop loss is 1.5–2× ATR from entry
2. **Position sizing check** — high ATR relative to price means higher risk per share; reduce `quantity_multiplier` in risk agent
3. **Volatility filter** — ATR / close > 5% means very volatile; penalise `initial_confidence` by 0.05

```python
# ATR as % of price — use this in risk agent prompts
atr_pct = atr_14 / close * 100
# > 3%  → elevated volatility, consider quantity reduction
# > 5%  → high volatility, penalise confidence
# > 8%  → extreme, block unless very high conviction
```

### Volume vs volume_sma20 — Conviction

| Ratio | Meaning |
|---|---|
| volume < 0.5 × volume_sma20 | Very low volume — signal is weak, reduce confidence |
| volume 0.8–1.2 × volume_sma20 | Normal — neutral |
| volume > 1.5 × volume_sma20 | Above-average — conviction behind the move |
| volume > 2.0 × volume_sma20 | High conviction — boost confidence by 0.03–0.05 |

### SMA 20 / SMA 50 — Trend Direction

```
close > sma_20 > sma_50  →  uptrend stack — BUY preferred
close < sma_20 < sma_50  →  downtrend stack — SELL/SHORT preferred
sma_20 crosses above sma_50  →  golden cross — medium-term BUY signal
sma_20 crosses below sma_50  →  death cross — medium-term SELL signal
```

---

## Market Context Rules

These fields come from `market_context` in the snapshot. They are macro-level and override symbol-level signals when extreme.

### VIX — Market Fear Gauge

Computed once per bar in `yahoo_feed._classify_market_sentiment()`:

| VIX level | Regime | Action |
|---|---|---|
| < 15 | Risk-on — complacency | Normal signal flow, full position sizes allowed |
| 15–20 | Normal | Standard operation |
| 20–25 | Elevated concern | Reduce `quantity_multiplier` to 0.75, tighten confidence floor |
| 25–30 | Risk-off | Reduce quantity to 0.5, BUY signals need extra confirmation |
| > 30 | Fear / crisis | Block BUY signals unless RSI < 25 + volume spike; SHORT/COVER allowed |
| > 40 | Extreme fear | Block all NEW BUY signals; only COVER (close shorts) passes |

### SPY Trend — Broad Market Direction

Computed by `yahoo_feed._classify_trend("SPY")` (price vs 20-day SMA ±1%):

| spy_trend | BUY signals | SELL/SHORT signals |
|---|---|---|
| uptrend | Preferred — high conviction | Require stronger confirmation (RSI > 70, volume spike) |
| sideways | Require multi-indicator agreement | Require multi-indicator agreement |
| downtrend | Require RSI < 30 + volume spike | Preferred — high conviction |

### Sector Flow

Derived from VIX in `_classify_market_sentiment()`:
- `risk-on` (VIX < 15) — growth and momentum stocks favoured
- `neutral` (VIX 15–25) — balanced
- `risk-off` (VIX > 25) — defensives favoured, reduce exposure to high-beta names

---

## Signal Confidence Calibration

The `initial_confidence` from the signal agent should reflect how many indicators agree. This is a reference table for prompt engineering and output validation:

| Indicators aligned | Suggested initial_confidence range |
|---|---|
| 1 indicator only | 0.60–0.70 — too weak, likely blocked by risk agent |
| 2 indicators agree | 0.70–0.80 |
| 3 indicators agree | 0.80–0.88 |
| 4+ indicators agree + volume confirms | 0.88–0.95 |
| All indicators + macro tailwind | 0.95–0.98 |

Rules:
- Never output `initial_confidence = 1.0` — no signal is certain
- `initial_confidence < 0.75` will almost always be blocked (debate −0.05 to −0.15, then risk floor 0.90)
- The signal agent should justify its confidence in `reasoning` by citing which specific indicators agree

### Confidence flow through the pipeline

```
signal_agent → initial_confidence (0.0–1.0)
    ↓ debate agent adjusts
debate_agent → adjusted_confidence (can go up or down ±0.15 typical)
    ↓ risk agent adjusts
risk_agent   → final_confidence (clamped 0.0–1.0)
    ↓ hard floor check
MIN_SIGNAL_CONFIDENCE (default 0.90 via env var) → block if below
```

A signal must survive all three stages. Design prompts so that genuinely strong setups reach 0.90+ after all adjustments.
