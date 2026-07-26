# Why No New Symbols in the Portfolio?

**Investigated:** July 25, 2026
**Author:** Codebase audit

---

## The Short Answer

There are **3 filters in a row** that prevent new symbols from entering the portfolio — and the first one is the most important: **the system only trades 5 specific ETFs, not stocks.**

---

## Filter #1 — The Symbol Universe is Fixed

The brain only ever looks at 5 symbols: **QQQ, GLD, TLT, EEM, XLE**

This is hardcoded in `ai-brain/data_feed/symbol_universe.py`:

```python
DUAL_MOMENTUM_UNIVERSE = ["QQQ", "GLD", "TLT", "EEM", "XLE"]

def get_symbols() -> list[str]:
    return DUAL_MOMENTUM_UNIVERSE
```

There's actually code in the same file to fetch 500 liquid stocks from Alpaca's API — but **it's dead code**. Nobody calls it. The function that's actually used (`get_symbols()`) just returns the 5 ETFs above.

The signal agent's instructions are also hardcoded to reject anything else (`ai-brain/agents/signal_agent.py`):

```
Universe: QQQ, GLD, TLT, EEM, XLE ONLY.
If the symbol is NOT in this list → output null immediately.
```

So the brain never even considers AAPL, TSLA, NVDA, or any other stock.

---

## Filter #2 — Only 1 ETF per Bar

Even among the 5 ETFs, the brain picks just **one** — the one with the strongest momentum (`main.py` lines 354-377). The other 4 are dropped with an activity log message saying "rotation: [winner] has stronger momentum — dual-momentum trades only the strongest ETF."

So at most, **1 symbol per 5-minute bar** enters the pipeline.

---

## Filter #3 — Deduplication

If that single surviving symbol already has an open position or a pending signal waiting for your approval — it gets dropped too (`main.py` lines 380-398).

---

## How These Filters Stack Up

Here's a concrete example of how no new symbols can enter:

| Step | What happens | Action |
|------|-------------|--------|
| Universe | Brain scans QQQ, GLD, TLT, EEM, XLE | Only 5 ETFs |
| Pre-filter | e.g. TLT's RSI is 50 (neutral) → dropped | 4 remain |
| Rotation | Picks strongest momentum (e.g. QQQ) | 1 survives |
| Dedup | You already hold QQQ → dropped | **0 left** |

**Result:** Zero new signals generated, every single bar.

If you hold 3 positions and have 2 pending signals, the entire universe is blocked — every single ETF either has an open position or a pending one. Nothing new can enter.

---

## What Would Need to Change

### Option A: Close positions + clear pending signals
This is the quickest fix. If you close some positions and approve/reject the pending signals at the Green Light gate, the dedup filter lifts and the pipeline can generate new trades.

### Option B: Expand the universe
The `get_symbols()` function would need to be changed to actually fetch the full liquid stock list from Alpaca instead of returning the hardcoded 5 ETFs. This opens the door to any stock, but would also need strategy changes since the current `dual_momentum` strategy is designed for macro ETFs.

### Option C: Allow multiple positions per bar
Instead of rotation picking just 1 winner, allow multiple symbols through if they meet the entry criteria.

---

## Current Bottleneck Summary

| Bottleneck | File | Line(s) | Impact |
|------------|------|---------|--------|
| 5 ETF hardcode | `symbol_universe.py` | 34, 42 | Only QQQ, GLD, TLT, EEM, XLE |
| Signal agent rejects non-ETFs | `signal_agent.py` | 64-65 | Everything else → null |
| Rotation drops 4 of 5 | `main.py` | 357-377 | 1 symbol per bar max |
| Dedup blocks held/pending | `main.py` | 380-398 | Nothing new if all 5 are blocked |
| Position cap at 10 | `portfolio_limits.py` | enforced | Hard limit on unique symbols |

The most impactful thing you can do immediately: **review the 487 pending signals at the Green Light gate** and approve or reject them. That alone will unblock the dedup filter and let the pipeline generate new trades.