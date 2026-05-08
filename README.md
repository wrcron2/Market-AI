# MarketFlow AI

A high-frequency multi-agent trading system with institutional-grade security and 90–100% signal accuracy.

## Architecture

```
┌─────────────────────────────────────────────────────┐
│                React 19 + TypeScript                 │
│            Dashboard  •  Green Light Gate            │
└────────────────────┬────────────────────────────────┘
                     │  WebSocket / REST
┌────────────────────▼────────────────────────────────┐
│              Go (Golang) Backend Server              │
│   IBKR API Client  •  gRPC Bridge  •  Staged Orders │
└──────────┬──────────────────────────┬───────────────┘
           │  gRPC                    │  SQLite/Postgres
┌──────────▼──────────┐   ┌──────────▼───────────────┐
│  Python AI Brain    │   │    Local Orders DB         │
│  LangGraph Agents   │   │  (staged, not executed)    │
│  Ollama / Bedrock   │   └──────────────────────────┘
└─────────────────────┘
```

## Security Guardrails

- **Green Light Gate** — ALL orders are STAGED in the local DB first. No trade touches the broker without an explicit manual Green Light from the dashboard.
- **Air-Gapped Keys** — Broker API keys never leave local hardware. Never sent to any cloud service.

## Tech Stack

| Layer | Technology |
|---|---|
| Frontend | React 19 + TypeScript + Vite |
| Backend | Go 1.22+ |
| AI Brain | Python 3.12 + LangGraph |
| Inter-service | gRPC + Protocol Buffers |
| Light LLM | Ollama (Qwen2.5-Coder 7B) |
| Heavy LLM | AWS Bedrock (Claude Sonnet 4.6) |
| Broker API | Interactive Brokers (IBKR) |

## Quick Start

### Prerequisites

| Tool | Version | Check |
|---|---|---|
| Go | 1.22+ | `go version` |
| Node.js | 20+ | `node -v` |
| Python | 3.12+ | `python3 --version` |
| Ollama | latest | `ollama list` |
| AWS account | — | for Bedrock (debate agent) |

### Step 1 — Pull the Ollama model

The AI brain uses Qwen2.5-Coder (7B) locally for fast, low-cost tasks. Pull it once (~4.5 GB):

```bash
ollama pull qwen2.5-coder:7b

# Verify it downloaded:
ollama list
```

### Step 2 — Configure your environment

```bash
cp .env.example .env
open .env   # Edit in any text editor
```

Minimum required to start in **Yahoo simulation mode** (no Yahoo API key needed — yfinance is free):

```env
# Only these two lines are required to get started:
AWS_ACCESS_KEY_ID=your_key_here
AWS_SECRET_ACCESS_KEY=your_secret_here
```

> AWS Bedrock is used for the debate agent (Claude Sonnet 4.6). If you skip AWS keys, the debate step will fail — the signal agent and risk agent (Ollama) will still work.

### Step 3 — Generate the gRPC stubs (one-time)

Compile `signals.proto` into Go and Python code:

```bash
# Install protoc + plugins (macOS):
brew install protobuf
go install google.golang.org/protobuf/cmd/protoc-gen-go@latest
go install google.golang.org/grpc/cmd/protoc-gen-go-grpc@latest
pip install grpcio-tools --break-system-packages

# Generate the stubs:
make proto
```

### Step 4 — Install all dependencies

```bash
make install
```

Or install each service manually:

```bash
cd frontend  && npm install && cd ..
cd ai-brain  && pip install -r requirements.txt --break-system-packages && cd ..
cd backend   && go mod tidy && cd ..
```

### Step 5 — Start the three services

Open three separate terminal tabs:

```bash
# Tab 1 — Go backend (port 8080 REST · 8081 WebSocket · 50051 gRPC):
cd backend && go run cmd/server/main.go

# Tab 2 — Python AI brain (polls Yahoo Finance every 5 minutes):
cd ai-brain && python main.py

# Tab 3 — React dashboard (port 3000):
cd frontend && npm run dev
```

Or use Docker Compose to run everything at once:

```bash
docker-compose up --build
```

### Step 6 — Open the dashboard

Navigate to **http://localhost:3000** in your browser.

You will see the MarketFlow AI dashboard with the trading mode toggle in the top-right corner:

```
Yahoo  [●────]  IBKR
       🧪 SIM
```

---

## Using the Dashboard

### A — Watch signals arrive

The AI brain fetches real Yahoo Finance data every 5 minutes, runs the full agent pipeline (signal → debate → risk), and pushes qualified signals to the dashboard via WebSocket. Each signal appears in the **"Awaiting Green Light"** queue with its confidence score and AI reasoning.

```
Yahoo data → Signal agent → Debate (bull vs bear) → Risk check → Your queue
```

### B — Review and Green Light a signal

Each signal card shows:
- **Symbol, direction, and quantity** proposed by the AI
- **Confidence bar** — colour coded (green ≥ 95%, yellow ≥ 90%, red < 90%)
- **Strategy name** and which model generated it
- **AI reasoning** — expand to read the bull argument, bear argument, and judge synthesis

Click **Green Light ✅** to execute, or **Reject ❌** to discard. You can add an optional note before either action.

In **Yahoo simulation mode**, execution is virtual — your virtual $100k portfolio is updated with a realistic fill price (yfinance latest price + 5bps slippage). No real money moves.

### C — Switch to IBKR when ready

Toggle the header switch from `Yahoo → IBKR` once you trust the signal quality.

Make sure TWS or IB Gateway is running locally:

```env
# .env — IBKR settings:
IBKR_HOST=127.0.0.1
IBKR_PORT=7497        # 7497 = paper trading, 7496 = live
PAPER_TRADING=true    # Keep true until you are fully confident
```

> ⚠️ In IBKR live mode, Green Light sends a real order to your broker. Always verify the symbol, direction, and quantity before approving.

---

## Trading Modes

| | Yahoo 🧪 SIM | IBKR ⚡ LIVE |
|---|---|---|
| Market data | yfinance (real, free, ~15 min delay) | IBKR TWS stream (real-time) |
| After Green Light | Virtual portfolio fill logged | Real order sent to IBKR |
| Yahoo API key needed | No — yfinance is free | — |
| Risk to capital | None — virtual $100k | Real capital at risk |
| Default | Yes | Off |

> **Yahoo Finance requires no API key.** The `yfinance` library uses Yahoo's public endpoints for free. For real-time data (< 15 min delay), swap the feed for Alpaca or Polygon.io (both have free API tiers).

## Directory Structure

```
market-ai/
├── backend/          # Go server (IBKR, gRPC, Green Light, WebSocket)
├── frontend/         # React 19 + TypeScript dashboard
├── ai-brain/         # Python LangGraph multi-agent system
├── infra/
│   ├── db/           # SQL schema for staged orders
│   └── proto/        # Protobuf definitions
├── docker-compose.yml
└── .env.example
```

## Green Light Workflow

```
AI Brain generates signal
        ↓
Go backend stages order in local DB (status: PENDING)
        ↓
Dashboard shows signal in "Awaiting Green Light" queue
        ↓
Trader reviews signal, risk score, and reasoning
        ↓
Trader clicks "Green Light" ✅  or  "Reject" ❌
        ↓ (only on Green Light)
Go backend sends order to IBKR API
```

> ⚠️ The AI can NEVER autonomously execute a trade. Human approval is always required.
