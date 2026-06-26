# MarketFlow AI — Claude Code Reference

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
Run backtest-expert methodology on both strategies before switching paper → live:
- `momentum_breakout`: MACD histogram expanding + volume > 1.5x SMA20 + price above SMA20
- `mean_reversion`: RSI < 25 or > 75 + Bollinger %B extreme + volume contracting
Requirements: 5+ years data, 100+ trades, out-of-sample ≥ 50% of in-sample performance.
See `backtest-expert` SKILL.md: `tradermonty/claude-trading-skills`

STATUS: Backtest infrastructure NOT yet built. This is the #1 P0 priority before live capital.
Architecture: `ai-brain/backtest/` — see ADR-001 in Notion Architecture Decision Records.
