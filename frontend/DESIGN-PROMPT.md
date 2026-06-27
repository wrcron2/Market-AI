# MarketFlow AI — New UI/UX Design Prompt for Claude

## Brief

Design a completely new, world-class UI for **MarketFlow AI** — a fully automated AI-driven trading system running 24/7. The target user is a solo trader/quant developer who monitors an autonomous system, reviews AI-generated signals, manages positions, and gets notified of critical events.

This is a **finance-grade professional dashboard**, not a consumer app. Think Bloomberg Terminal meets modern SaaS. Dark theme only. Data density matters — but clarity over clutter.

---

## System Context (read before designing)

**What the system does:**
- AI brain (Python) scans 5 macro ETFs (QQQ, GLD, TLT, EEM, XLE) every 5 minutes
- Generates trading signals using 3 AI agents: Signal → Debate (Bull/Bear/Judge) → Risk
- Executes trades autonomously on Alpaca paper trading during market hours
- Position monitor watches open trades and exits on SMA20 cross
- Alert system sends emails on critical events

**Current pages (tabs in old design):**
1. Signals — live signal feed + Green Light approval panel
2. Alpaca Portfolio — P&L, open positions, performance charts
3. Alerts — CRITICAL/HIGH/MEDIUM/INFO alerts
4. Audit Log — every order status transition
5. Pipeline — repo scout tool
6. Versions — Docker image history + rollback

**Key metrics always visible:**
- System status (market open/closed, AUTO_EXECUTE on/off)
- Today's P&L
- Open positions count
- Pending signals count
- Portfolio value (from Alpaca)
- Avg signal confidence

---

## Design Requirements

### Layout
- **Full-width, dark theme** — deep navy/slate (#0f1117 background family)
- **Two sidebars:**

  **LEFT sidebar (navigation — 240px wide, collapsible to 48px icon rail):**
  ```
  MarketFlow AI [logo]
  ─────────────────────
  Dashboard (overview)

  Trading
    > Live Signals
    > Green Light Queue
    > Open Positions
    > Trade History

  Performance
    > Portfolio Overview
    > P&L Analytics
    > Signal Accuracy
    > Equity Curve

  AI Pipeline
    > Signal Agent
    > Debate Log
    > Risk Gate
    > Position Monitor

  Alerts
    > All Alerts
    > Critical Only

  Audit Log

  System
    > Versions & Deploy
    > Configuration
    > Backtesting

  Pipeline (repo scout)
  ─────────────────────
  [Status bar: market open/closed]
  [Paper / Live mode indicator]
  ```

  **RIGHT sidebar (Ask AI — 360px wide, collapsible):**
  ```
  ┌─────────────────────────────┐
  │  Ask AI                     │
  │  ─────────────────────────  │
  │  Role: [dropdown]           │
  │  ┌─────────────────────┐   │
  │  │ - Chief PM           │   │
  │  │ - Engineering        │   │
  │  │ - Risk Analyst       │   │
  │  │ - Strategy Advisor   │   │
  │  └─────────────────────┘   │
  │                             │
  │  [Conversation history]     │
  │                             │
  │  ┌─────────────────────┐   │
  │  │ Ask a question...   │   │
  │  │                    ↑│   │
  │  └─────────────────────┘   │
  └─────────────────────────────┘
  ```

### Top Bar (60px)

**Left zone:** hamburger (collapse left nav) + breadcrumb

**Center zone — Global status pills:**
- `● Market Open` / `● Market Closed` (green/gray)
- `● Connected` / `● Reconnecting...` / `● Disconnected` (WebSocket health — green/amber/red with pulse animation when reconnecting)
- `⚡ AUTO ON` / `⏸ AUTO OFF` (read-only status pill — amber when ON, gray when OFF. Does NOT toggle on click. Clicking navigates to System > Configuration.)
- `Paper Trading` / `LIVE` mode indicator (paper = blue pill, live = red pulsing pill)
- `Portfolio: $101,979`

**Right zone:**
- **HALT ALL** button — persistent red button. Single click (no confirmation needed) triggers: (1) AUTO_EXECUTE → false, (2) cancels all pending Alpaca orders, (3) logs halt event to audit log. Always visible. This is the emergency kill switch.
- Alerts bell with count badge
- Settings gear
- Collapse right panel button

### Main Content Area
Fills remaining space. Each section has its own layout:

---

**Dashboard (Home):**
- 6 KPI cards row: Portfolio Value / Today P&L / Open Positions / Pending Signals / Win Rate / Avg Confidence
- 2-column: Live Signal Feed (left) + Active Positions table (right)
- Bottom: Recent Alerts strip

---

**Live Signals:**
- Real-time feed with event types: staged / approved / rejected / executed / debate_failed
- Each event: symbol chip, direction badge (BUY=green, SELL=red), confidence bar, strategy tag, AUTO badge if auto-executed
- Filter bar: by type, by symbol, by confidence
- Stale-data overlay appears when WebSocket is disconnected — semi-transparent overlay with "Connection lost — data may be stale" message

---

**Green Light Queue:**
- Cards for pending signals requiring approval
- Each card: symbol, direction, confidence bar (color-coded), strategy, AI reasoning (expandable), Bull/Bear/Judge summary
- Approve / Reject buttons with comment field
- Pulsing notification badge in left nav when signals are waiting
- **Desktop only for approvals** — mobile view shows queue as read-only (no approve/reject buttons)

---

**Open Positions:**
- Table: Symbol / Direction / Entry Price / Current Price / P&L / P&L% / Entry Time / Confidence / Actions
- SMA20 exit indicator (shows distance to exit trigger)
- Manual close button per row

---

**P&L Analytics:**
- Equity curve (SVG line chart)
- Win rate by strategy
- Performance by VIX regime
- Sharpe ratio, max drawdown cards
- Fill quality column: Alpaca fill price vs. signal limit price (post-trade surveillance)

---

**AI Pipeline:**
- Visual pipeline diagram showing the 5 nodes: Generate → Debate → Risk → Submit → Execute
- Per-node status indicators: idle (gray) / processing (blue pulse) / success (green) / error (red)
- Per-node stats: last execution time, average latency (e.g., "Risk Gate: 1.2s avg, last run 3s ago")
- Error state: last error message + timestamp when a node fails
- Last signal log per node

---

**Trade History:**
- Full table of all executed trades with outcome
- **Replay button** per trade — reconstructs the exact pipeline state at trade time: market snapshot, signal agent output, bull/bear/judge debate transcript, risk assessment, final execution. Rendered as a visual timeline in the main content area. Built from existing audit log data — no new infrastructure.

---

**Alerts:**
- Severity cards at top: CRITICAL / HIGH / MEDIUM / INFO with counts
- Alert list with color-coded glow borders
- Filter by severity
- AUTO_EXECUTE state changes logged as HIGH alerts automatically

---

**System > Configuration:**
- **AUTO_EXECUTE toggle** — this is the ONLY place to enable/disable autonomous execution. Requires a purpose-built modal with typed confirmation: operator must type "ENABLE" to activate. Shows warning: "The system will place orders automatically during market hours without your approval."
- **LLM Provider toggle** — switch between Ollama (local) and AWS Bedrock
- **Trading Mode** — Paper / Live selector (Phase 3)
- **Min confidence threshold** slider
- **Max position quantity** input
- Every configuration change logged to audit trail with timestamp

---

## Visual Style Guide

### Colors
```
Background:     #0f1117 (base)
Surface:        #1e293b (cards)
Surface-hover:  #1a2236
Border:         #334155

Accent-blue:    #3b82f6
Accent-green:   #22c55e (BUY, profit, positive, connected)
Accent-red:     #ef4444 (SELL, loss, critical, disconnected, HALT)
Accent-orange:  #f97316 (HIGH alerts, reconnecting)
Accent-yellow:  #eab308 (MEDIUM alerts, AUTO active warning)
Accent-purple:  #7c3aed (AUTO-executed trades)

Text-primary:   #e2e8f0
Text-secondary: #94a3b8
Text-muted:     #64748b
```

### Typography
- Font: `Inter` or `Geist` (system font fallback: `-apple-system`)
- Monospace: `JetBrains Mono` or `Fira Code` for prices, signal IDs, confidence values
- Numbers: tabular figures (`font-variant-numeric: tabular-nums`)

### Components
- **Cards**: `background: #1e293b`, `border-radius: 12px`, `border: 1px solid #334155`
- **Badges/pills**: small, rounded, color matches severity
- **Confidence bar**: thin horizontal bar, color: red → yellow → green based on value
- **Status dots**: 8px circle, animated pulse for live states
- **Charts**: SVG only (no Chart.js), minimal gridlines, green fill for equity curve
- **Stale overlay**: semi-transparent `rgba(15, 17, 23, 0.75)` with centered warning text, shown when WebSocket disconnects
- **HALT button**: `background: #ef4444`, white text, always visible in top bar, subtle glow on hover

### Micro-interactions
- Hover: `background` lightens to `#1a2236`, subtle `transform: scale(1.01)`
- New signal arrival: card slides in from right with green flash
- Alert: entry animates with colored glow border
- Left nav items: active state = blue left border + blue text
- WebSocket reconnecting: connection pill pulses amber
- HALT button: red glow intensifies on hover

---

## Ask AI Panel — Detailed Spec

**Dropdown options:**
```
Chief PM         → Strategic decisions, PRD format, priority assessment
Engineering      → Architecture, code patterns, technical trade-offs
Risk Analyst     → Position sizing, drawdown analysis, risk metrics
Strategy Advisor → Signal quality, backtest interpretation, entry/exit rules
```

**Data injection pipeline:**
Before each query, the panel calls `GET /api/context-snapshot` which returns:
```json
{
  "portfolio_value": 101979.23,
  "today_pnl": 1203.45,
  "open_positions": [...],
  "pending_signals": [...],
  "auto_execute": false,
  "market_status": "open",
  "last_signal_time": "2026-06-27T14:30:00Z",
  "pipeline_health": { "signal": "ok", "debate": "ok", "risk": "ok" }
}
```
This context is injected into the system prompt so the AI can reference real-time state.

**LLM routing:**
The Ask AI panel routes through a **cloud LLM** (Claude API), NOT local Ollama. This prevents GPU resource contention with the signal pipeline during market hours.

**Rate limiting:**
Max 10 queries per minute. Show "Rate limit reached — wait Xs" when exceeded.

**Behavior:**
- Remembers conversation context per role
- Each role has a system prompt pre-loaded
- Shows typing indicator while waiting
- Response renders markdown (bold, code blocks, tables)
- Quick-action buttons: "Review my signals", "Check risk", "What should I do today?"
- References real-time data from `/api/context-snapshot`

---

## Additional Features

1. **Command palette** (`Cmd+K`) — search across all signals, positions, alerts

2. **Mini chart on position cards** — sparkline showing price since entry

3. **Confidence heatmap** — signal history calendar view (GitHub-style), colored by confidence

4. **Market hours countdown** — shows "Market opens in 2h 34m" or "Market closes in 45m"

5. **Real-time portfolio ticker** — top bar shows live P&L updating every 30s

6. **Mobile-responsive** — on small screens: right panel collapses to floating button, left nav becomes bottom tab bar. **Mobile is read-only monitoring** — show status, alerts, positions, but no trade approvals. Green Light decisions must happen on desktop.

7. **Keyboard shortcuts** — `G` then `S` → Signals, `G` then `P` → Portfolio, `A` → Approve selected signal (desktop only), `R` → Reject, `Cmd+Shift+H` → HALT ALL

8. **Export button** — on any data table, export to CSV

9. **Decision Replay** — every trade in Trade History has a "Replay" button that reconstructs the exact pipeline state at trade time (market snapshot, signal output, debate transcript, risk assessment, execution). Visual timeline view. Built from existing audit log data.

---

## Stack Context for Implementation

- **React 19** + TypeScript
- **Vite** build tool
- **Tailwind CSS** (or pure CSS variables — no Bootstrap/MUI)
- SVG charts only (no external chart library)
- WebSocket connection for real-time data (with reconnection logic and health indicator)
- REST API: Go 1.24 backend on port 8080
- Ask AI panel: Claude API (cloud) — separate from pipeline's local Ollama

---

## Deliverable

Design the complete new dashboard in a single self-contained HTML file (can use inline CSS/JS). Include:
1. Full left sidebar with navigation hierarchy
2. Top status bar with: market status, WebSocket health, AUTO status (read-only), trading mode, portfolio value, HALT ALL button
3. Main Dashboard (home) view fully rendered
4. Right Ask AI panel (open state)
5. Show 2-3 example signal cards in the Green Light queue
6. Show 2 example open positions
7. Show example alerts
8. System > Configuration page with AUTO_EXECUTE toggle (typed confirmation modal)
9. WebSocket disconnected state overlay on one panel (to show the pattern)

Make it look like a production-grade financial platform that a professional quant would trust with real money.
