# MARKETFLOW AI — SYSTEM STATUS REPORT
### Chief PM Orchestrator | Live Oracle Audit | July 3, 2026
### (Delta vs. [status-report-2026-06-29.md](./status-report-2026-06-29.md))

---

## Section 1 — Executive Strategy

MarketFlow AI on Oracle has moved from $105,469.07 to **$110,129.95 equity** (+4.42% since the June 29 audit, +10.13% all-time on $100,000 seed capital). Two new real pipeline trades executed since June 29 — **QQQ long (112 sh)** and **EEM short (112 sh)** — bringing executed trades to 19 (from 17). This is the first evidence in two audits that the Green Light-approved pipeline is actually placing trades, not just seed positions.

That progress surfaces a more serious problem than the one it replaces. **QQQ was sized to $79,811 — 72.5% of account equity, ~9x the account's own 8%-of-equity position cap** [VERIFIED: `ai-brain/agents/signal_agent.py:172` — `"Cap: min(shares, 500, int(8000 / close))"`]. That cap is a sentence in a prompt fed to an uncalibrated 4B local model, not a code-enforced guard. I searched `ai-brain/execution/` and `backend/internal/` for any server-side position-size check — **there is none** [VERIFIED: zero matches for cap/allocation-limit logic outside the prompt string]. QQQ now sits beside the pre-existing AMP position (still $78,266, unchanged) as two oversized holdings that together are **75.2% of long market value and 143.6% of equity on margin**. The June 29 report's AMP-concentration finding was a legacy seed-position artifact with an excuse. This one is not — it is the live pipeline, mid-2026, proving the sizing rule is decorative.

Meanwhile the structural neglect flagged last time has gotten worse, not better: **pending signals grew from 484 to 487**, and the oldest unreviewed signal in the queue is now dated **2026-05-23** — over five weeks stale [VERIFIED: sqlite query on `staged_orders`]. One genuine win: the Judge role in the debate agent has been upgraded from qwen3:4b to **deepseek-r1:7b** [VERIFIED: `ai-brain/agents/router.py:29,37,65,86`, commit `623bc27`], fulfilling half of the P1 "Debate Agent → DeepSeek-R1" priority from the June 26 assessment. Bull/Bear and Signal/Risk remain on qwen3:4b. `AUTO_EXECUTE` correctly remains `false` — the re-validation endpoint it depends on is still unbuilt [VERIFIED: zero matches for "revalidate" in `backend/` or `ai-brain/`].

---

## Section 2 — Core Product Requirements (Live Data Audit)

### Account Summary

| Metric | June 29 | July 3 | Δ |
|--------|---------|--------|---|
| Equity | $105,469.07 | **$110,129.95** | +$4,660.88 (+4.42%) |
| Buying Power | $271,712.21 | $179,341.85 | -$92,370.36 |
| Cash (margin used) | -$19,667.66 | **-$92,801.54** | -$73,133.88 |
| Long Market Value | $125,136.73 | $210,289.89 | +$85,153.16 |
| Short Market Value | $0 | **-$7,358.40** | new (EEM short) |
| All-Time Realized P&L | +$4,673.82 | +$4,673.82 | unchanged — no new closes |
| Today's Realized P&L | +$5,750.96 | $0.00 | 0 trades today |
| Daily Loss Limit | NOT halted | NOT halted | — |

Margin usage nearly 5x'd, driven almost entirely by the QQQ position and the new EEM short. `trading_limits` for 2026-07-03 shows 0 trades closed today.

### Open Positions (13, sorted by exposure)

| Symbol | Side | Mkt Value | Unrealized P&L | P&L % | % of Long MV | % of Equity |
|--------|------|-----------|-----------------|-------|---------------|--------------|
| **QQQ** | LONG | **$79,811.20** | -$789.64 | -0.98% | **38.0%** | **72.5%** |
| **AMP** | LONG | **$78,265.60** | +$5,509.90 | +7.57% | **37.2%** | **71.1%** |
| TLT | LONG | $9,577.12 | -$211.68 | -2.16% | 4.6% | 8.7% |
| EXPD | LONG | $8,210.93 | +$615.93 | +8.11% | 3.9% | 7.5% |
| EEM | **SHORT** | -$7,358.40 | +$108.64 | +1.46% | -3.5% | -6.7% |
| EWJ | LONG | $6,985.50 | +$158.54 | +2.32% | 3.3% | 6.3% |
| PDI | LONG | $5,344.00 | +$12.47 | +0.23% | 2.5% | 4.9% |
| CQQQ | LONG | $4,589.15 | +$158.10 | +3.57% | 2.2% | 4.2% |
| BXSL | LONG | $3,803.20 | +$14.40 | +0.38% | 1.8% | 3.5% |
| BNL | LONG | $3,400.00 | +$112.09 | +3.41% | 1.6% | 3.1% |
| MUFG | LONG | $3,297.60 | +$191.87 | +6.18% | 1.6% | 3.0% |
| ACYN | LONG | $3,288.00 | -$59.20 | -1.77% | 1.6% | 3.0% |
| FENI | LONG | $1,975.19 | +$55.32 | +2.88% | 0.9% | 1.8% |
| CGBD | LONG | $1,742.40 | +$2.12 | +0.12% | 0.8% | 1.6% |

**QQQ + AMP combined = $158,076.80 = 75.2% of long market value, 143.6% of equity (margin-amplified).** No other position exceeds 8.7% of equity — the entire book's risk is concentrated in two names.

### Signal Pipeline

| Metric | June 29 | July 3 | Δ | Status |
|--------|---------|--------|---|--------|
| Total Signals Generated | 501 | 506 | +5 | — |
| Approved | 17 | 19 | +2 | — |
| Rejected | 0 | 0 | — | **RED FLAG — persists** |
| Executed | 17 | 19 | +2 | first real pipeline fills since audit began |
| **Pending (unreviewed)** | **484** | **487** | **+3** | **CRITICAL — still growing** |
| Oldest pending signal | not measured | **2026-05-23** (41 days old) | — | **CRITICAL — new finding** |
| Signals w/ confidence = 1.0 | flagged qualitatively | **16, all `ollama/qwen3:4b`** | — | **CRITICAL — quantified, unresolved** |

---

## Section 3 — Market Microstructure and Regulatory Architecture

**Reg NMS Rule 611**: N/A — mode is `yahoo` (paper sim), `is_live: false`. PASS.

**Green Light Gate**: STRUCTURALLY PRESERVED, **OPERATIONALLY WORSE**. The backlog grew (484→487) and its age is now quantified at 41 days for the oldest entry — this is not a gate being exercised slowly, it is a gate that has stopped being exercised at all for most of the queue. The 2 new executions (QQQ, EEM) prove approvals *can* happen; they also prove the review process, when it does run, is not catching sizing violations 9x over policy. Before Phase 3, the daily review workflow gap identified June 29 remains the top operational risk, now with a second failure mode layered on top: **approval without size validation**.

**FIX 5.0 / ETI**: N/A — no live execution path. PASS.

**PSD2/PSD3**: N/A — no payment processing. PASS.

**Position Sizing Rule (CLAUDE.md — ATR-based, 1% account risk)**: **FAIL, VERIFIED**. `ai-brain/agents/signal_agent.py:172` encodes the 8%-of-account cap as prompt text for qwen3:4b to self-apply. QQQ's actual fill (112 sh @ $719.65 = $80,601 cost basis) is proof the model does not reliably obey it. No downstream system — not `alpaca_executor.py`, not the Go backend, not a DB constraint — re-checks position size before or after execution. This is a Level 2 (Architecture) gap under the instruction hierarchy, not yet Level 1, because it has not touched a live order-execution venue — but it is the exact failure mode Level 1 exists to prevent once IB/live execution is switched on.

---

## Section 4 — Algorithmic and AI Considerations

### LLM Router Status (`ai-brain/agents/router.py`)

| Role | Model | Verified |
|------|-------|----------|
| Signal Agent (LOW) | `qwen3:4b` (Ollama, local CPU) | `router.py:27,36` |
| Bull / Bear (HIGH) | `qwen3:4b` (Ollama, local CPU) | `router.py:28` |
| **Judge (HIGH_REASON)** | **`deepseek-r1:7b`** (Ollama, local CPU) | `router.py:29,37,65,86`, commit `623bc27` |
| Risk Agent | `qwen3:4b` (Ollama, local CPU) | unchanged |

This is genuine progress against the June 26 P1 priority ("Switch HIGH complexity calls from qwen3:4b to deepseek-r1:7b") — but only the Judge moved. Bull/Bear, the actual adversarial debate that should be catching a QQQ-style sizing outlier before it reaches Green Light, is still on the uncalibrated model. All 487 pending signals in the DB are attributed to `ollama/qwen3:4b` — none have been re-scored by the upgraded Judge yet, since Judge only runs at debate time, not retroactively.

**Confidence calibration**: 16 pending signals carry `confidence >= 0.999`, all from `qwen3:4b` — unchanged finding from June 29, now with an exact count. These signals would pass any reasonable confidence gate unchecked.

**Position-size enforcement**: See Section 3. This is the standout new algorithmic gap this cycle — a prompt-level safety rail with zero code-level backstop.

### Container Health

| Container | June 29 | July 3 | Note |
|-----------|---------|--------|------|
| backend | 2 days | **Up 3 min** | restarted very recently — cause not identified in this audit, flag for follow-up |
| brain | 2 days | 5 days | stable, no restart |
| frontend | 43 hours | **Up 3 min** | restarted alongside backend |

Backend and frontend restarted together minutes before this audit ran; brain did not. Could be a routine redeploy or an unrelated crash/restart — worth a one-line check of `docker logs` next session, not urgent enough to block this report.

---

## Section 5 — Gap Analysis and Edge Cases

### RED: Immediate Action Required

1. **[NEW, CRITICAL] QQQ position sized at 72.5% of equity, ~9x the account's own 8% cap.** The cap exists only as prompt text (`signal_agent.py:172`) with no code-level enforcement anywhere in `ai-brain/execution/` or `backend/internal/`. This is the pipeline itself violating policy, not a legacy seed position.
2. **AMP + QQQ = 75.2% of long market value, 143.6% of equity on margin.** A 10% adverse move across both = -$15,808, or 14.4% of total equity. Portfolio has no working diversification control.
3. **487 unreviewed pending signals, oldest dated 2026-05-23 (41 days stale).** The Green Light gate is not being exercised at the rate signals are generated. This is worse than June 29, not better.
4. **16 signals with hallucinated `confidence: 1.0`, all from `qwen3:4b`.** Unresolved since last audit; now with an exact count and model attribution.

### YELLOW: Near-Term Risks

5. **Margin usage grew 4.7x** (-$19,668 → -$92,802), driven by QQQ and the new EEM short. Fine in Yahoo Sim; must be modeled and capped before Phase 3.
6. **Re-validation endpoint at 9:25am still not implemented** — confirmed via code search, matches June 29 finding. Pre-market `_requires_revalidation` signals remain inert.
7. **Bull/Bear debate still on qwen3:4b** — the model most responsible for catching a sizing/confidence outlier before Green Light is the one not yet upgraded.
8. **backend + frontend containers restarted ~3 minutes before this audit ran**, cause unverified this session.
9. **Email notifications still not configured** (unchanged from June 29 — not re-verified this session, carried forward).

### GREEN: Working as Designed / Improved

10. **Pipeline executed 2 real trades since June 29** (QQQ, EEM) — first confirmed evidence of end-to-end signal → approval → execution flow working, sizing bug aside.
11. **Judge upgraded to deepseek-r1:7b** — P1 priority partially delivered.
12. **`AUTO_EXECUTE` correctly still `false`**, gated on the still-missing re-validation logic — the system is not prematurely enabling automation it isn't ready for.
13. No failed orders (`/api/orders/failed` empty). No new alert noise since June 27 (still 16 total, all duplicate startup alerts, not growing).
14. Brain container stable 5 days uninterrupted.

---

## Section 6 — The Out-of-the-Box Catalyst

**Position Guard + Signal Triage, unified.** June 29 proposed a Signal Triage Dashboard to fix the review bottleneck. This audit shows the deeper issue: even the signals that *do* get reviewed and approved aren't being checked against the account's own risk policy before they reach Alpaca. The fix is one Go-side service, not two: a **deterministic Position Guard** sitting between `/api/orders/approve` and the Alpaca execution call, which independently recomputes `min(shares, 500, floor(0.08 * live_equity / price))` from live account equity — a hard code path, not a prompt suggestion — and rejects or clamps any order exceeding it, logging a HIGH alert either way. Layer the triage ranking (urgency, staleness, confidence delta) on top of the same service so it also auto-expires the 41-day-old backlog. One deployment closes both the June 29 gap (review bottleneck) and the July 3 gap (unenforced sizing) with code the existing Go backend already has the scaffolding for (`/api/orders/approve`, `/api/orders/pending`). This is the highest-leverage single change available before Phase 3.

---

### Self-Consistency Checklist

- Reg NMS Rule 611: **PASS** (Yahoo Sim, no live orders)
- Green Light gate preserved: **PASS** (code intact; operationally worse than June 29 — flagged)
- FIX 5.0 / ETI specified: **N/A**
- PSD2/PSD3 compliance: **N/A**
- Stack compatibility (React 19 / Go 1.22 / Python 3.12): **PASS**
- All 6 PRD sections present: **PASS**
- Code scan performed: **PASS** — `ai-brain/agents/signal_agent.py:172`, `ai-brain/agents/router.py:27-86`, `backend/cmd/server/main.go` routes, live sqlite query on `staged_orders`/`alerts`, live Alpaca account/positions via `/api/alpaca/*`
