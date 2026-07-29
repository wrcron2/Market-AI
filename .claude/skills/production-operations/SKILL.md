---
name: production-operations
description: How to reach, inspect, deploy, and roll back the live MarketFlow AI system on the Oracle Cloud VM. Use when deploying, checking what production is actually doing, reading production logs, debugging a container, or diagnosing why the pipeline produced no signals or trades.
---

# Production Operations — Oracle VM

## The first rule

**The local checkout is not the running system.** Production is a separate `git pull`
of `main` at `/home/ubuntu/Market-AI` on the Oracle VM, running Python 3.12 in Docker.

Files under `logs/` and `infra/db/marketflow.db` in the local checkout are local
artifacts and are usually stale or empty. `logs/ai-brain.log` in particular is a fossil
from April 2026, from a directory that no longer exists by that name. **Never conclude
anything about system behavior from local logs or the local database.** Check
production instead — see "Inspect" below.

## The second rule — merged is NOT shipped

**A fix that is not running in production is not a fix.** If you commit a bug fix
or a decision to `main`, deploy it in the same session and verify it live.

This is not style advice. On 2026-07-29 the pipeline was found to have made zero
trades for 12 days with no alert, because commit `138bd06` — the business-outcome
monitoring written *specifically to catch that failure* after the 2026-07-25
incident — sat on `main` for three days and was never deployed. Production ran
`96d60b3`. The safety net existed, was correct, and was useless.

- Deploy in the same session as the push. Docs-only pushes are exempt.
- If you genuinely cannot deploy (market open with an open position, missing
  credentials), say so explicitly and record what is pending. Never leave it silent.
- Verify **behavior**, not the build: hit the endpoint, read the log line, run
  `preflight.py`. A green `docker-compose up` proves nothing.
- `preflight.py`'s `deploy_drift` check compares the deployed HEAD to
  `origin/main`. If it fails, production is running code that is not `main` —
  that is a live incident, not a chore.

## Access

`ssh oracle` → `ubuntu@129.159.146.157` (alias defined in `~/.ssh/config`).
App root on the host: `/home/ubuntu/Market-AI`.

## Inspect (do this before diagnosing anything)

```bash
curl -s http://129.159.146.157:8080/api/positions          # open positions
curl -s http://129.159.146.157:8080/api/orders/pending      # Green Light backlog
ssh oracle 'sudo docker ps'
ssh oracle 'sudo docker logs market-ai-brain-1 --tail 50'
ssh oracle 'cat /home/ubuntu/Market-AI/logs/brain_heartbeat.json'
```

Use `/api/orders/pending` as the liveness probe, **not `/api/stats`** — that 500s on an
empty DB. (Note `preflight.py` and `watchdog.sh` do use `/api/stats`; that's a known
inconsistency, not a reason to copy it.)

Other log locations on the host: `logs/preflight.log`, `logs/watchdog.log` (cron
output), `logs/brain_heartbeat.json` (written every loop; the watchdog alerts if it's
older than 15 minutes).

## Deploy

**GitHub `main` is the only source of truth. Never rsync or scp files up.** The flow is
always: push to `main` → pull on the host → rebuild.

```bash
ssh oracle "cd /home/ubuntu/Market-AI && git pull origin main"
ssh oracle "sudo docker-compose -f /home/ubuntu/Market-AI/docker-compose.yml up -d --build brain backend frontend"
```

Use `sudo docker-compose` (hyphenated). Verified on the host 2026-07-28: the binary at
`/usr/local/bin/docker-compose` is **Compose v2.27.0** installed under the hyphenated
name, and `docker compose` (with a space) does not exist there. The hyphenated
container names below follow from v2 semantics.

Versioned alternative, run *on* the host — tags images and records history under
`infra/versions/`:

```bash
./scripts/deploy.sh              # auto version YYYYMMDD-HHMM
./scripts/deploy.sh 20260728-01  # explicit
./scripts/rollback.sh --list     # show available versions
./scripts/rollback.sh 20260727-1430
```

### Verify after every deploy

1. Load the dashboard and confirm the JS bundle hash changed.
2. `curl -s http://129.159.146.157:8080/api/orders/pending` answers.
3. `ssh oracle 'sudo docker logs market-ai-brain-1 --tail 50'` — no crash loop.

A green build is not a deploy. Verify against the live system.

## Containers, ports, and services

Verified container names: `market-ai-brain-1`, `market-ai-backend-1`,
`market-ai-frontend-1`. Confirm with `ssh oracle 'sudo docker ps --format "{{.Names}}"'`.

| Port | Serves |
|---|---|
| 8080 | Go backend REST + WebSocket (`/ws`) |
| 3000 | nginx SPA; proxies `/api` and `/ws` → `backend:8080` |
| 50051 / 50052 | backend / brain gRPC — published but unused (live channel is REST+WS) |
| 11434 | Ollama, on the **host** not in Docker; containers reach it via `host.docker.internal` |

Note `129.159.146.157:9000` is Market-AI-Factory, a **different product** — not this app.

## Timing and safety

- Prefer deploying **outside 09:30–16:00 ET** when positions are open. Deploy timing is
  a risk decision, not just an ops one.
- The SQLite order store is capital-critical. Back up `marketflow.db` **plus its `-wal`
  and `-shm` files** with the writer stopped.
- Never prune volumes.
- Env changes require editing the **server** `.env` **and** recreating the container —
  a restart alone will not pick them up. The Oracle `.env` differs from local and is
  edited only on the server.
- `scripts/fresh-start.sh` is destructive (wipes trading data to a $100K baseline). It
  refuses to run without a backup younger than 24h, and the Alpaca paper account must be
  reset manually in the web UI first.

## Monitoring already in place

- `scripts/oracle/preflight.py` — 11:00 UTC Mon–Fri, 11 checks including a
  business-outcome check (fails if pending backlog > 20 or zero distinct symbols over 5
  sessions).
- `scripts/oracle/watchdog.sh` — every 10 min, 13–21 UTC Mon–Fri. Checks heartbeat
  freshness, backend liveness, and that `market-ai-brain-1` is running.

Both alert via `POST /api/alerts` and Resend email, throttled to 1/hour per check.
Backlog triage is **human-only — do not bulk approve or reject** staged orders.
