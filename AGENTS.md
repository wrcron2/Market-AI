# AGENTS.md — MarketFlow AI

Read this first. Repo also has `CLAUDE.md` (Claude-specificNotes); this file is the canonical agent guide and supersedes conflicting prose in the README when it comes to current wiring.

## Stack (3 services, 3 runtimes)

- **ai-brain/** — Python 3.12 LangGraph multi-agent system. Entry: `ai-brain/main.py`. Polled loop, 5-min bar cycle, sleeps outside US market windows (ET).
- **backend/** — Go 1.24 server. Entry: `backend/cmd/server/main.go`. REST :8080, WebSocket :8081 (`/ws`), gRPC :50051. SQLite at `infra/db/marketflow.db` (WAL).
- **frontend/** — React 19 + TS + Vite. Dev :3000. `npm run dev`. Lint: `npm run lint` (eslint, `--max-warnings 0`).

Intents travel **Brain → HTTP REST → Backend → WebSocket → Frontend**. gRPC is also defined (`infra/` / `backend/proto/`) but the live Brain↔Backend channel is REST/WS, not gRPC.

## Dev commands

```bash
# All-in-one (kills stale :8080/:50051/:3000, starts backend + frontend)
./start.sh

# Individual services
make backend         # cd backend && go run cmd/server/main.go
make brain           # cd ai-brain && python main.py
make frontend        # cd frontend && npm run dev

# Install all deps (note --break-system-packages for pip)
make install

# Docker (builds all 3 services, host Ollama must already be running)
docker-compose up --build
make docker-up  /  make docker-down
```

`make proto` is documented but its source path (`infra/proto/signals.proto`) does not exist — the real `.proto` lives at `ai-brain/proto/signals.proto`. Generated stubs are committed at `ai-brain/proto/` (Py) and `backend/proto/` (Go); treat them as build artifacts and don't hand-edit.

## Tests

- Python agents: `python -m pytest ai-brain/tests/ -v` (LLM is mocked; no network needed). Single: `python -m pytest ai-brain/tests/test_portfolio_limits.py -v`.
- LLM-provider toggle E2E (needs backend running): `./scripts/test_llm_provider_e2e.sh [http://localhost:8080]`.
- No Go test suite yet.
- Backtest gate (Phase 3): `python3 -m backtest run --strategy all` from repo root — note the module path; results land in `backtest_results/`. Gate thresholds are hard-coded in `ai-brain/backtest/report.py` and enforced.

## Operational flow (non-obvious)

- **Execution venue is Alpaca** (paper now, live gated at Phase 3). `backend/internal/ibkr/client.go` is a STUB, not the current path. Don't "fix" it to use IB — see `CLAUDE.md` Execution Venue ADR.
- **Brain refuses to start without Alpaca.** `ai-brain/main.py` calls `AlpacaExecutor().verify_account()` before the loop. Paper keys must be in `.env`.
- **Green Light gate**: backend stages orders in SQLite `staged_orders` (status `PENDING`); nothing hits the broker without a human click from the dashboard.
- **Portfolio limits are deterministic** (`ai-brain/agents/portfolio_limits.py`, wired after `risk_agent`). Prompt-level caps are advisory and WILL be ignored by the LLM — never rely on prompt caps alone (2026-07-09 QQQ 80%-position incident).
- **Cash-only mode** (`CASH_ONLY_MODE=true`): blocks shorts and over-cash buys across auto-execute, Green Light, and retry. Alpaca is margin by default; we enforce cash behavior in software.
- **Signal deduplication** has two gates: pipeline gate (`pending+open symbols` filtered in `main.py`) and execution gate (Alpaca position re-check).
- **Trading mode** is toggled at runtime via dashboard → `/api/mode`; `TRADING_MODE` in `.env` is only the start default.

## Deploy (Oracle host, not local)

- `./scripts/deploy.sh [VERSION]` — builds + tags all images, writes `infra/versions/history` + `current`, runs `docker-compose up -d`. Default version `YYYYMMDD-HHMM`.
- `./scripts/rollback.sh <version>` switches to a saved version.
- `./scripts/fresh-start.sh` wipes trading tables (keeps research + cost telemetry) — REFUSES to run without a 24h backup in `~/backups/`, and requires a manual Alpaca paper reset in the web UI first. Run on Oracle.
- `infra/systemd/marketflow-watcher.service` runs `scripts/version-watcher.sh` for version switches. Don't disable without blowing up the rollback mechanism.

## Conventions that aren't obvious

- **Python permits `--break-system-packages`** for pip installs (used in Makefile + README). Use it for one-off installs on this machine.
- **No automated CI** — verification runs locally (pytest + eslint + `go vet`). `go vet` is pre-allowed in `.claude/settings.json`.
- Agent prompts live in `ai-brain/agents/`; **repo scout pipeline prompts live in `agents/` (root)** but the scout now runs natively in the Go backend (`backend/internal/pipeline`) — the root `agents/*.md` files are reference-only, not the runtime.
- Trading strategies have lifecycle gates; live status is in `CLAUDE.md` under "STATUS". Don't revive `momentum_breakout` (retired) without a new gate pass.
- `frontend/dist/`, `node_modules/`, and `infra/db/*.db*` are gitignored — built/local state only.
- OpenCode/Claude skills live in `.claude/skills/` (not `opencode.json`); notable: `model-router` (cheapest-capable-model dispatch), `product-notion-sync` (MANDATORY after main pushes, per `CLAUDE.md`), `marketflow-chief-pm` (PRD workflow).

## Things that will trip an agent up

- Editing `backend/internal/ibkr/` thinking it's the live broker path — it's a stub.
- Trusting README's "IBKR" framing — execution is Alpaca now (see CLAUDE.md ADR).
- Running `make proto` and failing — source `.proto` is in `ai-brain/proto/`, not `infra/proto/`.
- Editing committed `*.pb.go` / `*_pb2.py` stubs by hand — regenerate or leave alone.
- Placing real IBKR/Alpaca/AWS secrets in committed files — `.env` is gitignored; use `.env.example` as the schema.
- Skipping the post-push Notion sync (per `CLAUDE.md`) after behavior-changing main pushes.
