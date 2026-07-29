      # Fork Factory — Phased Plan

Goal: turn MarketFlow AI from one trading system into a platform that runs many
("fork a repo → get a working, isolated trading product, all controlled from one parent").

Rule for every task below: it fits in one Claude Code session, and it ends with
something you can SEE working (a file, a passing test, a running container).
Never start a task whose phase-predecessor isn't done.

---

## Phase 0 — Decide before building (the ADR)  ← START HERE
The goal doc itself says this needs an ADR. Everything else depends on these 3 decisions.

- [ ] 0.1 Write ADR: **isolation model** — compare (a) Docker-compose stacks on one server,
      (b) separate repos + separate servers, (c) separate cloud accounts. Pick one with costs.
- [ ] 0.2 Write ADR: **parent→child auth** — how the controller talks to each product
      (per-child API key? mTLS? shared secret per product?). Pick one.
- [ ] 0.3 Write ADR: **findings format** — one JSON schema every child uses to report
      status/risk/optimize-results upward. Pick one.
- [ ] 0.4 Review the 3 decisions together, commit `docs/adr/00X-fork-factory.md`.

## Phase 1 — Make MarketFlow itself forkable (the template)
You can't build a generic template from scratch — you extract it from the one product that works.

- [ ] 1.1 Audit: list every hardcoded thing in MarketFlow (ports, DB paths, Alpaca keys,
      symbols, strategy names, Notion IDs). Output: `docs/fork-factory-hardcoded.md`.
- [ ] 1.2 Introduce a single `product.yaml` config (product name, ports, DB path,
      broker keys env-var names, strategy list). Backend + brain + frontend read from it.
- [ ] 1.3 MarketFlow itself runs from `product.yaml` with zero behavior change
      (smoke test via run-market-ai skill proves it).
- [ ] 1.4 Create the template repo: MarketFlow minus its specific strategies/keys,
      plus a `setup.sh` that asks for name + keys and produces a runnable product.
- [ ] 1.5 Prove it: fork the template into a dummy product ("testproduct") and get it
      running end-to-end on paper trading with one trivial strategy.

## Phase 2 — Isolation (one box, many products)
Per the Phase-0 ADR decision. Assuming docker-compose stacks:

- [ ] 2.1 Parameterize docker-compose: one command spins up a product's full stack
      (backend, brain, frontend, own SQLite/DB volume) on its own port range.
- [ ] 2.2 Each product gets its own Alpaca paper account creds via its own `.env` —
      verify two products trade simultaneously without touching each other's positions.
- [ ] 2.3 Deploy story: script that deploys product N to Oracle alongside MarketFlow
      without breaking MarketFlow.

## Phase 3 — Parent controller
- [ ] 3.1 Product registry: a small service (or just a table + Go endpoints) listing
      every child: name, base URL, auth credential, status.
- [ ] 3.2 Read-only first: controller polls each child's existing health/positions
      endpoints, shows a cross-product dashboard page (equity, open positions, P&L per product).
- [ ] 3.3 Aggregated risk view: total exposure across all products, per-symbol overlap
      (two products both long NVDA = concentrated risk).
- [ ] 3.4 Kill switch: one button per product → child suspends trading (reuse the
      drawdown-suspend mechanism in `portfolio_limits.py`). Then a global kill-all.
- [ ] 3.5 Auth per the Phase-0 ADR — controller credentials to children, children
      reject unauthenticated calls.

## Phase 4 — "Optimize All" agent swarm
- [ ] 4.1 Define the report schema (from ADR 0.3): findings list with severity,
      category (bug / bad strategy / improvement), evidence, suggested fix.
- [ ] 4.2 One agent first: an engineer-review agent that runs against one product's
      repo and produces a findings report in that schema.
- [ ] 4.3 Add the quant/finance reviewer agent (strategy quality, risk model, backtest honesty).
- [ ] 4.4 Orchestrate: "Optimize All" endpoint on the controller → runs both agents
      against the chosen product → stores the report.
- [ ] 4.5 Dashboard button + report viewer page.

---

Progress notes: check boxes off as tasks land; each task = one branch/commit to main
(with Notion sync per CLAUDE.md).
