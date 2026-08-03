"""Execution lane (Trading System v2.4 plan, §5).

Broker-as-truth inversion: the broker's state is the only truth about
positions and orders; local state is a cache rebuilt from the broker on every
startup and reconnect (§5.1, lane.py). Alongside the cache, an append-only
intent journal records every command the system sent, its envelope parameters,
and the broker acknowledgment (§5.2, journal.py) — the journal is a record of
intent, never a competing source of truth about state.

Modules:
- broker.py   — BrokerGateway contract + SimulatedBroker (offline, file-
    persisted "broker side" used by the tests; the live adapter implements
    the same contract).
- journal.py  — IntentJournal: append-only JSONL with an explicit write-
    behind buffer, so an unflushed crash is a real, testable event.
- envelope.py — order envelopes and clamps (§5.3): max order notional,
    trades-per-day, per-sleeve caps. Pure boundary logic.
- lane.py     — ExecutionLane: resync protocol v2 (§5.1), submit flow through
    the envelope, bracket-coverage verification and re-arm, orphan scans.
- ca_guard.py — corporate-actions guard (§5.5): reconcile open brackets
    against the CA calendar before session start; cancel/rebuild around
    ex-dates so a split never leaves an orphaned bracket.

Offline, stdlib-only, no network — same constraints as the rest of the v2.4
validation track. Gated tests: tests/test_envelope_clamps.py (§5.3),
tests/test_chaos_resync.py (§5.1 + §5.6), tests/test_ca_guard.py (§5.5).
"""
