# Kimi Learner decision authority

Status: implemented locally, release gates still in progress, activation disabled.
This document describes source behavior, not a deployed service or actual
Alpaca receipt. Fake-broker results are not investment-performance evidence.

## Ownership and modes

```text
Kimi owned frozen evidence → explicit model economics → saved signed intent
    → Market mechanical safety → paper broker → confirmed fill/owned position
    → signed feedback → learner record and separate prediction evaluation
```

`DECISION_AUTHORITY` is captured at startup. Only exact `KIMI` or `LEGACY`
selects a runtime. Every other value is `DISABLED`; no fallback occurs.

- DISABLED: health and configuration status only.
- KIMI: dedicated learner routes, SQLite journal and HMAC nonce store; no
  legacy handlers, watcher, strategy engine or gRPC listener. The Python brain
  stays in its non-decision heartbeat loop.
- LEGACY: retained old runtime, still subject to paper/learning safeguards.
  Economic Python work additionally requires exact paper operating mode.

Supported learner orders: long-only US equities, explicit quantity, DAY limit
orders. HOLD makes no order. EXIT requires exactly the confirmed learner-owned
quantity. Unsupported, stale, conflicting or unsafe requests are rejected;
Market never rounds, clamps, changes side, substitutes a symbol or chooses a
different investment. A known client ID alone does not establish ownership.

## Configuration (do not activate from this example)

The disabled handoff keeps `KIMI_EXECUTION_ENABLED=false` and
`KIMI_KILL_SWITCH=true`. No real credentials belong in source or reports.

| Setting | Requirement |
|---|---|
| `DECISION_AUTHORITY` | `KIMI` for this service; absent means DISABLED |
| `MARKET_AI_OPERATING_MODE` | exact `paper` |
| `PAPER_TRADING` | exact `true` |
| `ALPACA_BASE_URL` | exact `https://paper-api.alpaca.markets`; defaults to that host |
| `ALPACA_API_KEY`, `ALPACA_SECRET_KEY` | paper credentials, server-only |
| `KIMI_EXECUTION_DB` | absolute persistent dedicated SQLite path |
| `KIMI_OWNER_ID` | exact learner owner ID |
| `KIMI_ALPACA_ACCOUNT_ID` | broker-verified account, equal on both sides |
| `KIMI_EXECUTION_HMAC_KEY` | shared server-only key, at least 32 bytes |
| `KIMI_EXECUTION_POLICY_VERSION` | explicit version, equal on both sides |
| `KIMI_MAX_ORDER_NOTIONAL`, `KIMI_MAX_POSITION_NOTIONAL` | positive explicit account-currency decimal limits |
| `KIMI_MAX_PORTFOLIO_NOTIONAL`, `KIMI_MAX_DAILY_NOTIONAL` | positive explicit limits; no inferred defaults |
| `KIMI_OBSERVATION_MAX_AGE_SECONDS` | integer 1–60; learner currently uses at most 30 seconds |
| `GO_SERVER_HOST` | defaults to `127.0.0.1`; do not expose publicly |

Incomplete policy disables submissions. Enabling requires both exact
`KIMI_EXECUTION_ENABLED=true` and `KIMI_KILL_SWITCH=false`, plus all checks;
it is a separate operational approval, not part of running tests. Settings are
immutable for the process: changing an environment file does not change an
already running service.

Kimi configuration: `KIMI_TRADING_OWNER_ID`, `KIMI_ALPACA_ACCOUNT_ID`,
`KIMI_EXECUTION_POLICY_VERSION`, `MARKET_AI_EXECUTION_URL`, the shared key, and
`KIMI_TRADING_ENABLED=false` at handoff. Its existing LLM configuration is used
only when a separately enabled new decision is requested. Apply
`db/migrations/20260922_learner_trade_intents.sql` explicitly to an approved
database; startup never migrates. Keep local demo authentication local only.

## HTTP boundary

All learner routes, including reads, require HMAC authentication. Sign exact
`METHOD + "\n" + path/query + "\n" + timestamp-seconds + "\n" + nonce + "\n" +
SHA256(raw-body)` using the shared key. Headers: `X-Kimi-Timestamp`,
`X-Kimi-Nonce`, `X-Kimi-Signature`. Nonces are UUIDs and persist in SQLite;
timestamps must not be future or more than 60 seconds old. Retries use new
nonces but identical saved intent bytes. Kimi's `MarketClient` handles this.

| Route | Effect |
|---|---|
| GET `/api/learner/observations?symbol=AAPL` | Fresh paper account/IEX data and journal-owned quantity |
| POST `/api/learner/intents` | Validate, reserve, claim, then submit at most once or reconcile |
| GET `/api/learner/intents/{decisionId}` | Lookup/reconcile saved identity; never submit a replacement |
| DELETE `/api/learner/intents/{decisionId}` | Request cancellation of that verified learner order only |

Responses preserve original intent/hash, broker identity, cumulative fills,
safety measurements and the latest 100 audit events. This is not a full audit
export. Missing or inconsistent broker evidence means reconciliation required,
not zero fill. A cancellation acknowledgement does not prove cancellation.
Position evidence is a separately refreshed observation, not a per-order fill
misrepresented as current account holdings. P&L stays unavailable when complete
cost basis, fees or valuation evidence is missing.

## Offline proof

Never run the ordinary stack just to obtain dependencies. Use disposable
MySQL ending in `_test`, a separately compiled test binary and loopback only:

```sh
# Market backend, with existing cached modules:
GOPROXY=off GOSUMDB=off go test ./... -count=1
GOPROXY=off GOSUMDB=off go test -race ./internal/learnerexecution
GOPROXY=off GOSUMDB=off go vet ./...
go test -c ./internal/learnerexecution -o /absolute/scratch/learner-http.test
# Kimi worktree, use an explicitly disposable local DB:
STUDY_TEST_DATABASE_URL='mysql://LOCAL_TEST_DB' \
MARKET_TEST_BINARY=/absolute/scratch/learner-http.test \
node --import tsx scripts/verify-learner-execution.ts
```

The placeholder DB URL is deliberately unusable; provide the real disposable
loopback `_test` URL privately. The script never loads `.env`. It uses an injected
synthetic model and a fake HTTP broker. Market's test-only transport blocks all
external dialing; production has no fake-host switch. The script kills Market
after broker acceptance, restarts the same journal, verifies unresolved status
during broker unavailability, then recovers the one existing fill. It checks
BUY, SELL, cancellation, ownership, exact economics/hash and repeated retries.
Test data and journal paths remain available as evidence, clearly synthetic.

Full Python regression uses Python 3.12 in a read-only, `--network none`
container, with source/dependencies mounted read-only and `/tmp` as tmpfs.
Compile `CGO_ENABLED=0 GOOS=linux GOARCH=arm64 go test -c ./cmd/server -o PATH`
for the current ARM64 test image, mount it at `/test-server`, and run
`python tests/run_offline_suite.py` with `PYTHONPATH` containing repo, ai-brain
and test dependencies, `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1`, and empty broker keys.
The fixture launches only the real provider-setting handler, never `main()`.
AWS remains disabled; the HTTP regression expects 403 and unchanged local mode.
Run frontend `npm run lint` and `npm run build` separately.

## Disable and recover (no destructive rollback)

1. Stop new learner decisions (`KIMI_TRADING_ENABLED=false`) and restart only
   the intended learner process with that immutable configuration.
2. Keep Market authority **KIMI**, but disable submissions and engage the kill
   switch; restart that dedicated service without changing its journal/account.
   Authenticated status, observations and order-specific cancellation remain
   available. No automatic liquidation occurs.
3. Reconcile accepted/partial/unknown orders by existing decision/client IDs.
   Broker unavailability or missing lookup means wait/check, never resubmit.
4. Preserve both databases and SQLite WAL-related state using proper backup
   tooling. Never delete the journal, reset request IDs or point the executor
   at an empty journal to “fix” uncertainty. Retired learner request IDs are
   permanent safety records, not disposable retry caches.
5. After outstanding learner orders/exposure are accounted for, an operator
   may explicitly select LEGACY. Setting authority DISABLED earlier removes
   reconciliation endpoints; it is an emergency stop, not a reconciliation
   workflow. No code path switches authority automatically.

Actual Alpaca paper execution, provider quality, profitability and production
deployment are separate unverified layers. Do not infer them from this test suite.
