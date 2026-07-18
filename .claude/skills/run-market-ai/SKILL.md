---
name: run-market-ai
description: Run, build, smoke-test, or screenshot the MarketFlow AI app locally — Go backend (:8080), React dashboard (:3000), Python brain. Use when asked to run/start/launch the app, verify a change works, or take a dashboard screenshot.
---

# Run MarketFlow AI (local dev, macOS)

Three services: Go backend (`backend/`, HTTP :8080 + gRPC :50051), React/Vite
dashboard (`frontend/`, :3000, proxies `/api` and `/ws` to :8080), Python AI
brain (`ai-brain/`). Production runs in Docker on Oracle — this skill is the
LOCAL path. All paths below are relative to the repo root.

## Agent path: the smoke driver (start here)

One command builds the backend, launches backend + frontend on scratch ports
(8090/50061/3100, throwaway DB — never collides with a running dev instance),
curls every health endpoint, screenshots the dashboard, and cleans up:

```bash
.claude/skills/run-market-ai/smoke.sh /tmp/mf-smoke
# exit 0 = healthy; screenshot at /tmp/mf-smoke/dashboard.png, logs alongside
```

Verified output ends `== 8 passed, 0 failed` (9 with a dev backend on :8080 —
the api-proxy check SKIPs otherwise).

## Run the real dev stack

```bash
# Backend — MUST run from backend/ (DB + prompt paths are cwd-relative)
cd backend && go build -o server ./cmd/server && ./server
# → "database ready" dsn=./infra/db/marketflow.db; Ctrl-C or kill to stop

# Frontend (second shell, repo root)
cd frontend && npm install && npm run dev        # → http://localhost:3000
```

Health probe: `curl localhost:8080/api/orders/pending` → 200. (NOT `/api/stats`
— see Gotchas.)

## Screenshot (headless Chrome — no chromium-cli on this machine)

```bash
"/Applications/Google Chrome.app/Contents/MacOS/Google Chrome" --headless \
  --disable-gpu --window-size=1440,900 --virtual-time-budget=8000 \
  --screenshot=/tmp/dash.png http://localhost:3000/portfolio
```

Any tab route works: `/signals /portfolio /reports /alerts /audit /versions
/pipeline /config`. Chrome prints allocator/installwebapp errors to stderr —
harmless; judge by the PNG.

## Checks per layer

```bash
cd frontend && npm run build     # tsc + vite — the frontend correctness check
cd backend && go vet ./...       # plus go build ./...
cd ai-brain && python3 -c "import main"          # deps sanity (global python3)
cd ai-brain && python3 -m backtest --help        # backtest CLI entry
```

Full backtest is `python3 -m backtest run --strategy all` (slow — downloads
data; only ran `--help` when authoring this). Full brain pipeline needs Ollama
on :11434 (`curl localhost:11434/api/tags`) and takes hours on CPU — don't
launch it as a smoke test.

## Gotchas (all hit for real)

- **`/api/stats` returns 500 on an empty DB** (aggregate over zero rows). Use
  `/api/orders/pending` as the liveness probe.
- **Two local SQLite files exist.** `infra/db/marketflow.db` (repo root) is the
  Docker-mounted one; `backend/infra/db/marketflow.db` is created by a dev
  server run from `backend/` (default DSN is cwd-relative). Don't mistake one
  for the other when inspecting data.
- **Stale dev servers squat on :8080 for weeks.** A `go run` process had held
  the port for 21 days with a broken DB, 500ing everything. If :8080
  misbehaves: `lsof -nP -iTCP:8080 -sTCP:LISTEN`, kill it, relaunch from
  current source.
- **`godotenv` loads `.env` from the process cwd** — a backend started in
  `backend/` does NOT read the repo-root `.env`. Expect a harmless
  "startup position sync failed: alpaca credentials not configured" warning.
- **eslint is broken** (no config file found) — `npm run lint` always fails;
  `npm run build` is the real frontend check.
- **No `timeout` command on macOS** — don't write shell that depends on it
  (the smoke driver doesn't).

## Troubleshooting

- `no such table: staged_orders` from sqlite3 → you opened the wrong/fresh DB
  file (see the two-DBs gotcha). Schema is embedded in
  `backend/internal/db/db.go` and auto-creates on server start.
- Vite port taken → `npx vite --port 3100 --strictPort` (what the driver does).
- Deploying to production is NOT `npm run dev` — push to main, then see the
  `devops-oracle` agent / CLAUDE.md (git pull + `sudo docker-compose up -d
  --build` on the Oracle box).
