---
description: Conventions for the three-stage AI trading signal pipeline (Generate → Debate → Risk)
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
