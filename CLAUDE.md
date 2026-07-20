# MarketFlow AI — Claude Code Reference

## Notion Sync — MANDATORY after every push to main
Every push to `main` that changes product behavior (feature, bug fix, architecture,
config) MUST be followed by a Notion update in the same session, using the
`product-notion-sync` skill (it defines what maps to which page and the exact formats).
Fetch "Current Flow & Status" first to get the next bug number. Pages are listed in
memory `reference_notion.md`. Docs-only or formatting-only pushes are exempt.

## Phase 2 — Alpaca MCP Integration
When upgrading AlpacaExecutor to MCP-based calls (Phase 2), use these exact tool names
from `tradermonty/claude-trading-skills` portfolio-manager skill:

```
mcp__alpaca__get_account_info       → equity, buying power, cash balance, status
mcp__alpaca__get_positions          → all holdings: symbol, qty, cost basis, market value, P&L
mcp__alpaca__get_portfolio_history  → equity curve, drawdown, historical performance
```

Current implementation uses raw httpx calls in `ai-brain/execution/alpaca_executor.py`.
Switch to MCP calls when setting up the Alpaca MCP server in Phase 2.

## Key Architecture
- Brain (Python) → REST HTTP → Backend (Go) → WebSocket → Frontend (React)
- Signal pipeline: signal_agent → debate_agent → risk_agent → orchestrator
- All agent prompts: `ai-brain/agents/`
- Position sizing: ATR-based risk model (1% account risk / stop_distance)
- Deduplication: pipeline gate (pending+open symbols filtered) + execution gate (Alpaca position check)

## Execution Venue Decision (ADR)
Production execution venue: **Alpaca** (paper now, live at Phase 3).
`backend/internal/ibkr/client.go` is a stub — IB API is NOT the current path.
Alpaca REST API handles all order execution via `ai-brain/execution/alpaca_executor.py`.

## Before Going Live (Phase 3 Gate)
Gate requirements (do NOT relax): 5+ years data, 100+ trades, out-of-sample ≥ 50%
of in-sample performance, Sharpe ≥ 0.5 on daily-equity returns, max drawdown ≤ 25%.
Engine: `ai-brain/backtest/` — real ^VIX merged, both-leg commissions, adverse
slippage, calendar-date 60/40 IS/OOS split. Run: `python3 -m backtest run --strategy all`
See `backtest-expert` SKILL.md: `tradermonty/claude-trading-skills`

STATUS (2026-07-17, honest engine):
- momentum_breakout: FAILED (Sharpe -0.67) — retired 2026-06-26, stays retired
- mean_reversion: PASSES gate (Sharpe 1.35, OOS 52% of IS, PF 1.90) — first pass with
  the real-VIX filter active; provisional, paper-validate before any deploy decision
- dual_momentum (LIVE strategy): FAILED honest gate (Sharpe 0.36 < 0.5). The June 26
  "pass" (0.79) was inflated by per-trade annualization assuming 5-day holds.
  Paper trading OK; Phase 3 live capital is BLOCKED until a strategy clears this gate.

## Portfolio Hard Limits (enforced in code since 2026-07-17)
`ai-brain/agents/portfolio_limits.py`, wired after risk_agent in the orchestrator:
10% max single position, 30% max sector, 10 max open positions, -15% drawdown suspend.
Prompt-level caps are ADVISORY ONLY — the 2026-07-09 QQQ incident (LLM ignored the
8% prompt cap, produced an 80% position) is why enforcement is deterministic code.
Position strategy_name is joined from staged_orders (same id) so the position
monitor's SMA20 trend exit works; empty strategy_name = legacy dual_momentum.

## graphify

This project has a knowledge graph at graphify-out/ with god nodes, community structure, and cross-file relationships.

Rules:
- For codebase questions, first run `graphify query "<question>"` when graphify-out/graph.json exists. Use `graphify path "<A>" "<B>"` for relationships and `graphify explain "<concept>"` for focused concepts. These return a scoped subgraph, usually much smaller than GRAPH_REPORT.md or raw grep output.
- If graphify-out/wiki/index.md exists, use it for broad navigation instead of raw source browsing.
- Read graphify-out/GRAPH_REPORT.md only for broad architecture review or when query/path/explain do not surface enough context.
- After modifying code, run `graphify update .` to keep the graph current (AST-only, no API cost).
