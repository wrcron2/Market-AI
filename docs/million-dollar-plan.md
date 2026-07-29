# MarketFlow AI — The Road to $1M

**Written:** 2026-07-26
**Status:** Diagnosis + plan. Not a promise.

---

## 0. The honest headline

**No trading plan works 100%. Any plan that claims to is lying to you.**

Markets are adversarial and partly random. The best professional quant funds in the
world target 15–25% a year and have losing years. So the goal of this document is
not to guarantee $1M — it is to give you the *highest-probability path* to it, and to
tell you honestly where you are on that path today.

Here is where you are today, measured from your own backtest engine
(`backtest_results/*_20260717_*.json`, the honest post-fix run):

| Strategy | Status | CAGR | Sharpe | Verdict |
|---|---|---|---|---|
| `dual_momentum` | **LIVE right now** | **0.79%/yr** | 0.36 | Fails gate. Worse than a savings account. |
| `mean_reversion` | Validated, not deployed | **8.26%/yr** | 1.35 | Passes gate. Roughly index-like. |
| `momentum_breakout` | Retired | negative | −0.67 | Correctly retired. |

The strategy you are running turns $100,000 into **$103,991 over five years.**

## 1. What that means for $1M

**Years to reach $1M, by strategy and starting capital:**

| Strategy (CAGR) | from $10k | from $50k | from $100k | from $250k |
|---|---|---|---|---|
| `dual_momentum` — what's live (0.79%) | 585 yr | 381 yr | 293 yr | 176 yr |
| `mean_reversion` — your best (8.26%) | 58 yr | 38 yr | 29 yr | 17 yr |
| Good outcome (15%) | 33 yr | 21 yr | 16 yr | 10 yr |
| Elite outcome (25%) | 21 yr | 13 yr | 10 yr | 6 yr |

**Flip it around — the return you'd need:**

| Deadline | from $10k | from $50k | from $100k | from $250k |
|---|---|---|---|---|
| 5 years | 151%/yr | 82%/yr | 58%/yr | 32%/yr |
| 10 years | 58%/yr | 35%/yr | 26%/yr | 15%/yr |
| 15 years | 36%/yr | 22%/yr | 17%/yr | 10%/yr |
| 20 years | 26%/yr | 16%/yr | 12%/yr | 7%/yr |

**Read the tables together and the conclusion is unavoidable:** anything above ~30%/yr
sustained is at or beyond world-class-fund territory. The only cells in these tables
that are realistically reachable are the ones needing **under ~25%/yr**. That means
**$1M is a capital problem at least as much as it is an algorithm problem.** Deposits
move you down these tables faster than any amount of code does.

The realistic framing: **get the system to a genuine 12–20%/yr, then feed it capital
and time.** That is the plan below.

---

## 2. What is actually broken (the real diagnosis)

Your infrastructure is genuinely good — pipeline, agents, gates, monitoring, deploy.
That is not the bottleneck. These four things are.

### Gap 1 — 90% of your money does nothing (the biggest single lever)

`ai-brain/backtest/strategy.py:77` sizes every position as:

```python
dollar_risk = account_size * 0.01     # risk 1% of account
quantity    = int(dollar_risk / stop_dist)
```

Combined with the 10% max-position cap in `portfolio_limits.py`, the average position
ends up near 10% of the account. And `dual_momentum` holds **one symbol at a time**
(`main.py` rotation picks a single winner from 5 ETFs).

So roughly **90% of the account sits in cash earning zero.** The arithmetic proves it:
125 trades × +0.32% average per trade should be ~40% of gains if fully deployed. Actual
account gain was 4.0%. That factor-of-ten difference *is* the idle cash.

*Concrete example:* you have $100k. A signal fires on QQQ. The system buys ~$10k of
QQQ. QQQ goes up 5% — you make $500, which is 0.5% on your account. The other $90k
did nothing. Meanwhile just buying and holding QQQ with the full $100k would have made
$5,000.

### Gap 2 — one strategy, five symbols, one position

`symbol_universe.py:35` hardcodes `["QQQ", "GLD", "TLT", "EEM", "XLE"]`, and only the
single strongest survives each bar. There is dead code in the same file that can fetch
500 liquid stocks from Alpaca — nothing calls it.

That gives you ~25 trades/year. **25 samples a year is far too few to ever know whether
you have an edge or got lucky.** `mean_reversion` traded 24 symbols and got 206 trades
with 8× the per-trade return — more shots on goal is most of why it wins.

### Gap 3 — the validated strategy isn't the deployed one

`mean_reversion` passes your Phase 3 gate. `dual_momentum` fails it. **You are running
the failing one.** That is the single most direct fix available, and it costs no
research at all.

Caveat that must be respected: `mean_reversion`'s out-of-sample Sharpe (0.84) is 52% of
in-sample (1.62) — it scrapes past your 50% bar. That is a *provisional* pass, not a
strong one.

### Gap 4 — validation isn't yet strong enough to bet real money on

Your engine does one 60/40 calendar split. That is one experiment. Missing:
walk-forward analysis, parameter-sensitivity sweeps, Monte Carlo trade-order shuffling,
and per-regime breakdown (2020 crash / 2022 bear / 2023–24 bull). Without those, a
"pass" can easily be one lucky split. Your own history proves the danger — the June 26
`dual_momentum` "pass" at Sharpe 0.79 was an artifact of a bad annualization
assumption, and the honest re-run gave 0.36.

---

## 3. The plan

Four phases. Each has an exit test. **Do not skip a gate — every disaster in this
project's history came from acting on an unvalidated number.**

### Phase A — Stop the bleeding (this week, ~2 days)

| # | Action | Why |
|---|---|---|
| A1 | Halt `dual_momentum` allocation | It earns 0.79%/yr. It is not worth the LLM spend. |
| A2 | Drain the pending-order backlog | 487 stale orders from the July 25 incident are noise. |
| A3 | Add cost-per-trade telemetry | If LLM cost/trade > profit/trade, the system loses money by running. Currently unmeasured. |
| A4 | Reconcile live fills vs backtest assumptions | Verify real slippage matches modeled slippage. If live is worse, every backtest number is optimistic. |

**Exit test:** you can state, in dollars, what one round-trip trade costs you in LLM +
commission + slippage.

### Phase B — Harden validation (2–3 weeks)

Build these into `ai-brain/backtest/`:

- **Walk-forward analysis** — rolling train/test windows, not one split. This is the
  single most important addition.
- **Monte Carlo** — shuffle trade order 1,000×, report the 5th-percentile drawdown.
  Tells you the bad-luck case, not the average case.
- **Parameter sensitivity** — sweep each parameter ±30%. If performance collapses,
  it's curve-fit, not an edge.
- **Regime breakdown** — separate 2020 / 2022 / 2023–24 results.
- **Correlation matrix** across strategies — needed before combining them.

**Exit test:** `mean_reversion` survives all five. If it doesn't, you learned it was
never real — which is a win, cheaply bought.

### Phase C — Build a real portfolio (1–2 months)

This is where the return actually comes from. Three moves, in order of impact:

1. **Raise capital utilization from ~10% toward 50–70%.** Target 5–8 concurrent
   positions instead of 1. This alone is worth multiples of everything else — but it
   raises drawdown proportionally, so it only happens *after* Phase B proves the edge.
2. **Widen the universe.** Wire up the dead `_fetch_alpaca()` code in
   `symbol_universe.py`. 24–100 liquid names instead of 5. More trades = statistical
   confidence + more compounding events.
3. **Add 2–3 uncorrelated strategies.** A trend follower, a mean reverter, and a
   carry/rotation sleeve draw down at different times, so the blend has a higher Sharpe
   than any single one. This is the only genuine free lunch in finance.

**Exit test:** blended portfolio ≥ 12% CAGR, Sharpe ≥ 0.8, max drawdown ≤ 20%, on
walk-forward — not on a single split.

### Phase D — Compound (ongoing, years)

1. Paper trade the blend for **90 days minimum**. Compare live vs backtest weekly. A
   >30% divergence means the backtest is wrong — go back to Phase B.
2. Go live small — 10–20% of intended capital.
3. Scale only on evidence: 6 months of live results tracking backtest within tolerance.
4. **Deposit regularly.** At 15%/yr, $100k + $2k/month reaches $1M in ~12 years;
   without deposits it takes 16.5. Contributions are the highest-certainty lever you
   have, and the only one that doesn't require being right about markets.

---

## 4. Answers to your specific questions

### Strategies
Keep `mean_reversion` (validated). Stop `dual_momentum` (0.79%). Leave
`momentum_breakout` retired. **Add:** a cross-sectional relative-strength sleeve, a
volatility-targeting overlay (size positions by inverse volatility rather than fixed
1%), and a defensive regime filter. Target three uncorrelated sleeves.

### AI models
Current routing is sound: local Ollama for cheap classification, cloud for reasoning.
Refinements: use **Fable 5** for research and strategy design (highest capability),
**Sonnet 5** for routine coding, local `deepseek-r1` for the debate agent, and
`qwen3:4b` **only for structured JSON** — your own July 24 measurement showed it can't
do plain-text decisions. Critically: **no LLM should decide position size.** Sizing is
deterministic code. The July 9 QQQ incident (LLM ignored an 8% cap and produced an 80%
position) is exactly why.

### Agents
You have the right Claude Code agents (`ml-quant`, `qa`, `backend-go`,
`devops-oracle`, `frontend-react`) plus `quant-analyst` and `risk-manager` from the
plugin. **Missing:** a `strategy-validator` agent whose only job is to try to *break* a
strategy before deployment — an adversarial reviewer, not a helpful one. Confirmation
bias is the main killer of quant systems, and a dedicated skeptic is the cheapest
defense.

### Skills
Existing set is good. **Missing three:** `walk-forward-validation` (the Phase B
method), `live-vs-backtest-reconciliation` (drift detection), and
`capital-allocation` (how to size across multiple concurrent strategies).

### Hooks
You have exactly one hook — graphify's navigation guard. **Nothing enforces trading
safety.** CLAUDE.md *says* strategy changes need `qa` sign-off, but nothing checks.
Add: a `PreToolUse` hook that blocks edits to `agents/`, `symbol_universe.py`, or
`portfolio_limits.py` without an accompanying fresh backtest, and a `Stop` hook that
warns when a session touched strategy code without re-running the gate. **A written
rule that isn't enforced is a rule you will break under time pressure.**

### Automation / scheduled tasks
Have: watchdog, preflight, heartbeat, EOD report, signal-silence + backlog alerts (all
good, all recent). **Missing:** weekly walk-forward re-validation, monthly
strategy-decay detection (alert when live Sharpe drops below 50% of backtest),
daily cost-vs-P&L reconciliation, and quarterly correlation refresh.

### Research
The highest-value research is not "find a new indicator." It is:
1. Why does `mean_reversion` work? If you can't explain the economic reason, it's
   probably curve-fit and it will stop working.
2. What is your actual realized slippage vs modeled?
3. Which regimes kill each strategy?

### Workflow
`brainstorm → spec → backtest → walk-forward → qa adversarial review → paper 90d →
live small → scale on evidence`. Never let a strategy skip a step, however good the
numbers look. Especially when they look good.

### Rules to add to CLAUDE.md
- No strategy goes live without walk-forward + Monte Carlo, not just a single split.
- No LLM output is ever trusted for position size — deterministic code only.
- Capital utilization changes require a fresh drawdown study first.
- Every live strategy is compared to its backtest weekly; >30% divergence auto-halts.

---

## 5. What I would do first

If you only do one thing this week: **turn off `dual_momentum` and start Phase B
validation on `mean_reversion`.** You are currently spending LLM tokens and taking
market risk to earn 0.79% a year, while a better-validated strategy sits unused.

The realistic honest destination: **a 12–20%/yr system**, reached in months of
disciplined validation — then $1M arrives through consistent contributions and a
decade-ish of compounding. Anyone promising that path in 2 years is selling something.
