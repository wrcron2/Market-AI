# Learning-Only Mode (Block 1)

This source change defaults Market AI to **learning-only** operating mode.
In that mode the decision and broker-mutation paths covered below are disabled;
account, positions, and health reads remain available. This is not a wholly
read-only process: startup position reconciliation still mirrors broker reads
into the local database, and the brain writes a heartbeat. These changes have
not been deployed or used to restart the stopped production stack.

## Operating mode resolution

`MARKET_AI_OPERATING_MODE` is read **once at process start** and is immutable
for the process lifetime. No API call can toggle it, and there is no UI switch.

| Value                                  | Mode       |
|----------------------------------------|------------|
| unset, empty, `learning`, `live`, `Paper`, `paper ` (any deviation) | `learning` (fail closed) |
| exactly `paper`                        | `paper` (legacy behavior) |

There is deliberately **no live mode**.

- Go: `backend/internal/operatingmode` (`Parse`, `FromEnv`).
- Python: `ai-brain/execution/operating_mode.py` (pure stdlib).
- Compose default: `MARKET_AI_OPERATING_MODE: "${MARKET_AI_OPERATING_MODE:-learning}"`
  on both the `backend` and `brain` services (override via shell env or `.env`
  interpolation — never by editing the default to `paper`).

## What learning mode disables

**Backend (Go)**
- REST middleware rejects every non-`GET/HEAD/OPTIONS` request with
  `423 {"error":"learning_only","message":"..."}` before any business handler
  runs (`operatingmode.Mode.Middleware`, wired in `cmd/server/main.go`). The
  check is `m != Paper`, so a zero-value or otherwise invalid `Mode` also
  denies — only exact paper permits mutations.
- gRPC unary interceptor rejects every service call with `FailedPrecondition`
  before any service handler runs (`operatingmode.Mode.UnaryServerInterceptor`,
  same `m != Paper` fail-closed semantics).
- The market-hours auto-execute watcher goroutine is never started;
  auto-execute stays off.
- Broker boundary: `alpaca.Handler.PlaceOrder` / `ClosePosition` call
  `Mode.CheckBrokerMutation` **before any network request** — including
  `ClosePosition`'s preliminary position GET — so a rejected attempt produces
  zero outbound calls. The mode is captured when the handler is constructed.

**Brain (Python)**
- `main.main()` branches into `_run_learning_mode()` before importing or
  instantiating the Orchestrator and before any scanning / position-monitor /
  rotation / outcome-checking loop. No Alpaca account verification (a broker
  call) happens in this branch.
- The process stays alive and writes the liveness heartbeat
  (`brain_heartbeat.json`) every 60 s with `mode=learning`,
  `decisions_enabled=false`, `execution_enabled=false`. The heartbeat is
  liveness only — it does not simulate business activity — and keeps the
  Compose brain healthcheck green.
- An explicit startup log (`marketflow.brain.learning_mode`) explains the
  disabled behavior.
- `AlpacaExecutor.place_order` / `close_position` raise `LearningModeError`
  before any network request unless the client was constructed under the full
  paper configuration. The mutation policy — operating mode, the explicit
  `PAPER_TRADING` value, and the actual raw broker base URL — is **captured at
  client construction**; later environment edits can neither authorize a
  learning-mode or live-host client nor de-authorize a valid paper one.

## What remains usable in learning mode

- All `GET` endpoints (positions, orders, reports, stats, Alpaca account /
  positions / equity-history reads), `GET /ws`, `/healthz`.
- `GET /api/operating-mode` →
  `{"mode":"learning","decisionsEnabled":false,"executionEnabled":false}`.
- Python read-only executor methods (`get_account`, `get_all_positions`,
  `get_position`, `get_latest_price`, market clock).

## Running paper (explicit, intentional only)

Paper requires ALL of the following; every mutation fails closed otherwise:

1. `MARKET_AI_OPERATING_MODE=paper` (exact),
2. `PAPER_TRADING=true` (explicitly set),
3. broker base URL exactly `https://paper-api.alpaca.markets` — a live host,
   any suffix/path, userinfo, or an insecure scheme fails closed at both the
   Go (`CheckBrokerMutation`) and Python (`mutation_block_reason`) boundaries.

## Compose hardening

All published application ports bind to loopback only, preserving variable
ports and defaults: `127.0.0.1:${GO_SERVER_PORT:-8080}:8080`,
`127.0.0.1:${GO_GRPC_PORT:-50051}:50051`, `127.0.0.1:3000:3000`. Persisted
volumes are untouched. The Go Alpaca HTTP clients now carry a bounded 15 s
timeout.

## Evidence (Block 1 verification, offline)

- `gofmt -l backend/` → clean (no output).
- `go vet ./internal/operatingmode/ ./internal/alpaca/` → clean
  (run with `GOPROXY=off`; `go.sum` was regenerated from the local module
  cache for the build and removed afterwards — the staging omitted it;
  `go.mod` verified unchanged).
- `go test ./internal/operatingmode/ ./internal/alpaca/` → **ok** (17/17):
  mode fail-closed matrix, 423 middleware (rejected request never reaches the
  handler; GET/HEAD/OPTIONS pass; zero-value and `Mode("invalid")` deny with
  423, handler never called — regression for `m != Paper` semantics),
  `/api/operating-mode` body, gRPC FailedPrecondition before handler
  (including zero-value/invalid Mode), broker mutation matrix (learning /
  missing `PAPER_TRADING` / live host / insecure scheme / userinfo / suffix →
  **zero** outbound fake-transport calls), valid paper config still places and
  closes via fake transport, learning-mode reads usable, bounded client timeout.
- `/usr/bin/python3 -m pytest ai-brain/tests/test_learning_mode.py` →
  **11/11 PASSED** (pytest 8.4.2, real httpx 0.28.1 / structlog 25.5.0;
  `/usr/bin/python3` is **Python 3.9.6**, older than production's 3.12 — the
  code uses `from __future__ import annotations` and ran clean, but 3.12
  should re-run it before merge). Also passes 11/11 via the file's standalone
  runner under Python 3.14.7 with third-party modules stubbed. Covers mode
  resolution, mutation policy matrix, heartbeat content + injected stop,
  learning branch never importing `agents.orchestrator` /
  `execution.alpaca_executor`, construction-captured executor policy
  (learning-mode / live-host / invalid-env clients stay blocked with zero
  recorded requests even after the environment is corrected to valid paper;
  valid-paper client keeps working after env wipe), reads usable in learning.
- `docker compose config --no-env-resolution` → renders
  `MARKET_AI_OPERATING_MODE: learning` on backend and brain, `host_ip:
  127.0.0.1` on all published ports, volumes unchanged; variable-port override
  (`GO_SERVER_PORT=9999`) interpolates correctly.

### Astra independent review (2026-09-22)

- A separate copy of the complete backend with this patch passed `go test ./...`
  with network module fetching disabled, including compilation of cmd/server.
- Two independently reproduced failures were corrected: invalid Go Mode values
  bypassing HTTP/gRPC guards, and Python environment drift authorizing a client
  still configured for a live broker host. Both reviewer regressions now pass;
  the live-host test used only an httpx MockTransport and recorded zero calls.
- The corrected pytest suite independently passed 11/11 with installed httpx
  and structlog on Python 3.9.6. The standalone suite also passed 11/11 on the
  installed Python 3.12.13 with missing dependencies stubbed. This does not
  substitute for testing the production image and full brain suite.
- Source inspection found pipeline.New and agents.telemetry imports do not
  launch decision-producing work. WebSocket reads discard incoming messages.
- No production startup, order mutation, database migration, commit, push, or
  deployment was performed. Full learning integration remains a separate block.

### Worker-only staging limits (resolved or qualified above)

- `backend/cmd/server/main.go` cannot be compiled in this workspace: its
  internal packages (`db`, `greenlight`, `pipeline`, `ws`, …) were not staged.
  The wiring edits are policy-only and gofmt-clean, but need `go build ./...`
  in the real checkout.
- The full existing brain test suite (`ai-brain/tests/`, e.g.
  `test_portfolio_limits.py`) was not staged here; only
  `test_learning_mode.py` ran.
