---
name: model-router
description: Route MarketFlow AI subtasks to the cheapest capable Claude model instead of running whole workflows on Fable. Use when planning or dispatching multi-step work (product decision, feature, deployment), when asked to "route", "decompose", "optimize token cost", or before the Chief PM orchestrates a complex task. Produces a routing table (subtask → model → justification → cost) and can dispatch + log via the Anthropic API.
---

# model-router — cheapest-capable-model dispatch

All paths relative to the repo root. The driver is
`.claude/skills/model-router/router.py` — a self-contained CLI. **Decompose
first, on the cheap model; spend Fable tokens only where the routing table
says so.**

## Run (agent path)

```sh
pip3 install --user anthropic   # one-time; needs ANTHROPIC_API_KEY in env

# Plan only — Haiku decomposes + classifies, table printed, nothing dispatched:
python3 .claude/skills/model-router/router.py "Add a Sharpe ratio card to Strategy Reports, then deploy" --max-subtasks 5

# Dispatch every subtask on its assigned model, log usage+cost to SQL:
python3 .claude/skills/model-router/router.py "<task>" --execute --db infra/db/marketflow.db
```

On Oracle (where `ANTHROPIC_API_KEY` lives in `.env`):

```sh
ssh oracle 'set -a; source /home/ubuntu/Market-AI/.env; set +a; cd /home/ubuntu/Market-AI && python3 .claude/skills/model-router/router.py "<task>" --execute'
```

Verified 2026-07-04 end-to-end on Oracle: plan run showed 48% estimated
savings vs all-Fable; `--execute` run dispatched Fable + Haiku live
(actual spend $0.069, 60% below the all-Fable estimate), Sonnet 5 path
smoke-tested, 3 rows logged to `model_routing_log`.

## Routing policy (implemented in `route()` — deterministic, not LLM)

| Axis | Values |
|---|---|
| Complexity | `low` → `claude-haiku-4-5` · `medium` → `claude-sonnet-5` · `high` → `claude-fable-5` |
| Reversibility | `irreversible` → force Fable · `guarded` → bump one tier · `safe` → no change |

Tier intent: **Fable** — Chief-PM product decisions, major architecture,
trading-logic changes, anything frozen post-deployment. **Sonnet 5** —
implementation, refactoring, tests, code review, agentic execution.
**Haiku** — deployments, config, formatting, boilerplate, summaries, and
the decomposition step itself.

**Escalation:** an attempt fails on refusal, truncation, output < 40 chars,
or self-reported `CONFIDENCE < 0.60` → retry **one tier up, once**. If that
also fails (or it was already Fable), the subtask is flagged
`human_review` — never auto-consumed (Green Light philosophy).

**Logging:** every attempt → `model_routing_log` (tokens in/out, cost, model,
escalated_from, confidence, duration). SQLite today
(`infra/db/marketflow.db`); the DDL in `router.py` is Postgres/Supabase-ready.
Tune routing over time with:

```sql
SELECT model, COUNT(*), SUM(cost_usd), AVG(confidence) FROM model_routing_log GROUP BY model;
```

## Gotchas (all hit while building this)

- **Sonnet 5 / Fable 5 reject `temperature`, `top_p`, `top_k` (400)** and
  manual thinking config. The dispatcher sends **no** `thinking` param:
  Sonnet 5 then runs adaptive thinking by default; Fable 5 thinking is
  always on (`{type: "disabled"}` is a 400 on Fable). Don't "fix" this by
  adding params.
- **Sonnet 5 tokenizer ≈ 30% more tokens than Sonnet 4.6** for the same
  text. Never reuse token counts across models — the driver calls
  `count_tokens` per assigned model for every estimate.
- **Pricing is date-sensitive:** Sonnet 5 bills $2/$10 per MTok through
  2026-08-31, then $3/$15 — `price_per_mtok()` switches automatically.
  Haiku $1/$5, Fable $10/$50.
- **Fable can refuse** (`stop_reason: "refusal"`, HTTP 200). The dispatcher
  opts into server-side fallbacks (`server-side-fallback-2026-06-01` beta,
  fallback `claude-opus-4-8`) and still treats a final refusal as a failed
  attempt → escalation/human-review path.
- Sonnet 5 thinking spends from `max_tokens` — that's why its cap (8192) is
  higher than Haiku's (4096). Truncation (`stop_reason: max_tokens`) counts
  as a failed attempt.
- Haiku 4.5 does **not** support `output_config.effort` — don't add it.

## Troubleshooting

- `ModuleNotFoundError: anthropic` → `pip3 install --user anthropic`
  (needed once per machine; already installed on the Oracle host).
- `authentication_error` → no `ANTHROPIC_API_KEY` in env. Locally there is
  none by design; run on Oracle with the `.env` sourced (command above).
- Escalation behavior can be sanity-checked offline (no API) by stubbing
  `router.dispatch_once` — see the three-scenario test in the repo history
  (haiku→sonnet `escalated_done`; double-fail → `human_review`; Fable fail
  → straight to `human_review`).
