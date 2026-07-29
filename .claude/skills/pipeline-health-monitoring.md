---
description: Mandatory business-outcome monitoring for any new or changed pipeline stage — infra-liveness checks alone are not sufficient. Use when adding a health check, adding a new agent/strategy stage, or reviewing why a pipeline "looked healthy" but produced nothing.
---

# Pipeline Health Monitoring

## The failure mode this exists to prevent

On 2026-07-25 the trading pipeline generated zero new signals and accumulated a
487-order pending-approval backlog for an unknown number of trading days, with
zero alerts. Every existing check (`scripts/oracle/preflight.py`,
`scripts/oracle/watchdog.sh`) passed the whole time, because they only check
**infrastructure liveness** — backend up, Ollama responding, heartbeat fresh.
None of them checked whether the pipeline was actually *doing its job*.

A system can pass every liveness check for weeks while producing zero economic
value. Liveness and correctness are different signals. Conflating them is how
this incident stayed invisible.

## It happened again four days later — and the reason matters more

On 2026-07-29 the pipeline was found to have made **zero trades for 12 days**
(289 consecutive bars blocked, last signal 2026-07-13). The check that would have
caught it — `check_signal_activity` — had already been written on 2026-07-26 in
response to the incident above. It was correct. It was on `main`. **It had never
been deployed.** Production ran the previous commit, whose preflight had 10
checks instead of 11, and cheerfully emailed "PASSED — system ready for market
open" every trading morning.

Two lessons, and the second is the one people skip:

1. **A monitoring gap is not closed when the code is written. It is closed when
   the code is running in production and you have watched it fire.** See the Ship
   Gate in `CLAUDE.md` and the `production-operations` skill. `preflight.py` now
   has a `deploy_drift` check that fails when the deployed HEAD differs from
   `origin/main`, precisely so this cannot recur silently.
2. **Monitor decisions, not just outputs.** The root cause was a rotation/dedup
   deadlock: the strongest ETF was also the held one, so the candidate was
   selected and then immediately dropped, every bar, forever. Nothing was broken;
   nothing was *deciding*. `main.py` now emits `brain.bar_decision` once per bar
   with an explicit verdict (`incumbent_retained`, `rotated`, `blocked_*`,
   `no_candidate`, `entering`). A trading system's health metric is **decision
   throughput**, not uptime — a system making zero decisions is failing no matter
   how many checks are green.

## The rule

**Every pipeline stage that produces an economically meaningful output
(a signal, an order, a decision) must have a corresponding business-outcome
check, not just an infra-liveness check.** When you add or materially change
a stage in `ai-brain/agents/`, `main.py`, or anything feeding the Green Light
gate, ask: *if this stage silently stopped producing output while every
service stayed "up," would anything alert?* If the answer is no, add a check
before considering the work done.

A business-outcome check answers "is this producing what it's supposed to,"
not "is the process running." Examples already in this codebase:
- `check_signal_activity()` in `scripts/oracle/preflight.py` — fails if
  `distinct_symbols_last_5_sessions == 0` (business-logic silence) or
  `pending_count > 20` (approval backlog running away). See that function for
  the concrete pattern: poll a small read-only backend stats endpoint, compare
  against a threshold, alert via the existing Resend/dashboard channel used by
  `watchdog.sh`.

## Windowing: use trading-session windows, not wall-clock windows, for silence detection

A wall-clock window like "48 hours" will false-alarm across every weekend and
holiday (Friday's last signal is 63-70h old by Monday morning). Use a
session-count window (e.g. "last 5 trading days had zero output") or an
exchange-calendar-aware window instead. A check that cries wolf on a
predictable schedule gets ignored — which recreates the exact blind spot this
skill exists to close. See the git history of `check_signal_activity()` for
a real example of this bug being caught and fixed before ship.

## Where the Green Light gate applies

Business-outcome checks may **alert on** the state of the pending-approval
queue (size, age, growth rate). They must never **act on** it — no
auto-approve, no auto-reject, no bulk mutation of pending orders. The Green
Light human-approval gate is non-negotiable (see the Chief PM persona's
Level 1 rules). A monitoring check's job ends at making a human aware; the
decision stays human.

## Before shipping a new check

1. Does it distinguish infra failure from business-logic silence in its alert
   message? (An engineer reading the alert should know which one it is
   without opening a dashboard.)
2. Does it use a session-aware window, not a naive wall-clock one?
3. Does it fail loudly on a malformed/missing response rather than silently
   defaulting to a passing value?
4. Has `qa` reviewed the diff before it's pushed to main? (See the
   `Business-Logic Change Gate` in the root `CLAUDE.md`.)
