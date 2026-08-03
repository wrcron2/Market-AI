"""Execution lane orchestration: resync protocol v2, submit flow, bracket
coverage verification and re-arm (plan §5.1, §5.2, §5.3).

Resync protocol v2 (§5.1) — runs identically on startup and on reconnect:
  1. fetch positions, open orders, recent executions from the broker;
  2. rebuild the local cache from exactly that data — the cache has no other
     source and no authority of its own;
  3. reconcile the durable journal against broker truth and explain any gap
     (§5.2): broker executions/orders the journal never recorded are
     re-journaled as recovered, and journaled commands that never reached the
     broker are named as lost intents — the journal reconstructs intent
     without becoming a competing truth;
  4. verify bracket coverage for every position: a position whose take-profit
     and stop-loss legs do not both cover its full quantity is NAKED; missing
     or partial legs are cancelled and rebuilt from the bracket spec, at the
     broker, before anything else may trade;
  5. only then re-arm. `submit()` refuses while disarmed.

Bracket coverage invariant: after every lane action (resync, submit, CA
guard), every position carries take-profit + stop-loss legs at the broker
each covering the full position quantity, and no protective order rests
against a symbol with no position or a stale quantity. `verify_coverage()`
and `find_orphaned_orders()` are the machine-checkable forms of "zero naked
positions" and "no orphaned brackets" (§5.6, §9 kill criteria).

Trades-per-day and sleeve exposure for the §5.3 envelope are derived from
broker truth (today's entry executions plus live entry parents; positions at
cost basis plus live entry notionals), never from local counters — a crash
cannot reset a limit by forgetting it.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from execution_lane import envelope as env
from execution_lane.journal import IntentJournal, utc_now_iso


@dataclass(frozen=True)
class BracketSpec:
    """Protective bracket geometry, as fractions of the reference price
    (position avg cost). Injected — no hidden defaults."""
    take_profit_pct: float
    stop_loss_pct: float


@dataclass
class CacheSnapshot:
    """The local cache: an exact copy of broker truth, rebuilt wholesale on
    every resync (§5.1). Never persisted, never consulted for truth."""
    positions: list = field(default_factory=list)
    open_orders: list = field(default_factory=list)
    executions: list = field(default_factory=list)


@dataclass
class ResyncReport:
    session_date: str
    positions: int
    open_orders: int
    executions: int
    recovered_fills: list = field(default_factory=list)   # exec_ids
    recovered_orders: list = field(default_factory=list)  # order_ids
    lost_intents: list = field(default_factory=list)      # intent_ids
    rebuilt_protection: list = field(default_factory=list)  # symbols
    naked_before: list = field(default_factory=list)      # symbols
    armed: bool = False


@dataclass
class CoverageReport:
    ok: bool
    naked: list = field(default_factory=list)  # symbols lacking full TP+SL


@dataclass
class SubmitResult:
    allowed: bool
    intent_id: str
    qty: int = 0
    reasons: tuple = ()
    order_id: str = None
    exec_id: str = None


class ExecutionLane:
    def __init__(self, broker, journal_path, clamps: env.ClampConfig,
                 bracket_spec: BracketSpec, clock=utc_now_iso):
        self.broker = broker
        self.journal = IntentJournal(journal_path, clock)
        self.clamps = clamps
        self.bracket_spec = bracket_spec
        self.cache = CacheSnapshot()
        self.armed = False
        self.session_date = None
        self._intent_seq = 0

    # --- identity -------------------------------------------------------------

    def _next_intent_id(self, prefix="I"):
        # journal seq is monotonic across restarts, so intent ids never
        # collide even after a crash
        self._intent_seq += 1
        return f"{prefix}{self.journal.durable_seq + len(self.journal.pending_entries()) + 1}"

    # --- §5.1 resync protocol v2 ------------------------------------------------

    def resync(self, session_date) -> ResyncReport:
        """Startup/reconnect protocol. Rebuilds the cache from broker truth,
        explains any journal gap, repairs bracket coverage, then re-arms."""
        report = self._resync_reconcile(session_date)
        self._repair_coverage_and_arm(report)
        return report

    def _resync_reconcile(self, session_date) -> ResyncReport:
        """Steps 1-3: rebuild the cache from broker truth and explain any
        journal gap. Split out so start_session() can run the §5.5 CA guard
        before step 4's generic naked-position repair — otherwise resync's
        blunt repair (correct for crash recovery) fires first on a
        split-desynced bracket and preempts the CA guard's own split-specific
        cancel/rebuild, corrupting its report and journal trail."""
        if not self.broker.is_connected():
            self.broker.connect()
        self.session_date = session_date
        report = ResyncReport(session_date=session_date, positions=0,
                              open_orders=0, executions=0)

        # 1-2. fetch truth, rebuild cache wholesale
        report.positions = len(self.broker.positions())
        report.open_orders = len(self.broker.open_orders())
        report.executions = len(self.broker.executions())
        self._rebuild_cache()

        # 3. journal reconciliation against broker truth (§5.2)
        journaled_fills = {e["exec_id"] for e in self.journal.durable_entries()
                           if e["kind"] in ("fill", "recovered_fill")}
        for exc in self.cache.executions:
            if exc["exec_id"] not in journaled_fills:
                self.journal.append("recovered_fill", origin="broker_resync", **exc)
                report.recovered_fills.append(exc["exec_id"])

        journaled_orders = set()
        for e in self.journal.durable_entries():
            if e["kind"] == "ack":
                journaled_orders.add(e["order"]["order_id"])
                for child in e.get("children", []):
                    journaled_orders.add(child["order_id"])
            elif e["kind"] == "protective_rebuild":
                journaled_orders.add(e["take_profit"]["order_id"])
                journaled_orders.add(e["stop_loss"]["order_id"])
            elif e["kind"] in ("recovered_order", "cancel"):
                journaled_orders.add(e["order_id"])
        for order in self.cache.open_orders:
            if order["order_id"] not in journaled_orders:
                self.journal.append("recovered_order", origin="broker_resync", **order)
                report.recovered_orders.append(order["order_id"])

        answered = {e["intent_id"] for e in self.journal.durable_entries()
                    if e["kind"] in ("ack", "rejected")}
        broker_intents = self.broker.known_intent_ids()
        for cmd in self.journal.durable_entries():
            if (cmd["kind"] == "command"
                    and cmd["intent_id"] not in answered
                    and cmd["intent_id"] not in broker_intents):
                self.journal.append(
                    "lost_intent", intent_id=cmd["intent_id"],
                    symbol=cmd["intent"]["symbol"], side=cmd["intent"]["side"],
                    qty=cmd["intent"]["qty"],
                    note="command journaled but never reached the broker "
                         "(crash between journal flush and placement); "
                         "not re-placed by resync — strategy re-decides")
                report.lost_intents.append(cmd["intent_id"])

        if report.recovered_fills or report.recovered_orders or report.lost_intents:
            self.journal.append(
                "gap_report", session_date=session_date,
                recovered_fills=report.recovered_fills,
                recovered_orders=report.recovered_orders,
                lost_intents=report.lost_intents,
                explanation=("durable journal ended at seq "
                             f"{self.journal.durable_seq} while the broker held "
                             "events the journal never recorded; broker-side "
                             "events re-journaled as recovered_*, journaled "
                             "intents absent from the broker named as "
                             "lost_intent"))
        self.journal.flush()
        return report

    def _repair_coverage_and_arm(self, report: ResyncReport) -> None:
        """Steps 4-5: verify bracket coverage for every position, repair any
        naked position from the bracket spec, then re-arm. Mutates `report`
        in place."""
        # 4. verify bracket coverage for every position; repair the naked
        coverage = self.verify_coverage()
        report.naked_before = list(coverage.naked)
        for symbol in coverage.naked:
            self._rebuild_protection(symbol)
            report.rebuilt_protection.append(symbol)

        # 5. re-verify from fresh broker truth, then re-arm
        self._rebuild_cache()
        self.armed = self.verify_coverage().ok
        self.journal.append(
            "resync", session_date=report.session_date,
            positions=report.positions, open_orders=report.open_orders,
            executions=report.executions,
            recovered_fills=len(report.recovered_fills),
            recovered_orders=len(report.recovered_orders),
            lost_intents=len(report.lost_intents),
            rebuilt_protection=report.rebuilt_protection, armed=self.armed)
        self.journal.flush()

    def _rebuild_cache(self):
        self.cache = CacheSnapshot(positions=self.broker.positions(),
                                   open_orders=self.broker.open_orders(),
                                   executions=self.broker.executions())

    # --- bracket coverage --------------------------------------------------------

    def _position(self, symbol):
        for pos in self.cache.positions:
            if pos["symbol"] == symbol:
                return pos
        return None

    def _legs(self, symbol):
        """Live protective orders on a symbol: {role: [orders]}."""
        legs = {"take_profit": [], "stop_loss": []}
        for order in self.cache.open_orders:
            if order["symbol"] == symbol and order["role"] in legs \
                    and order["status"] == "open":
                legs[order["role"]].append(order)
        return legs

    def verify_coverage(self) -> CoverageReport:
        """Every position must have TP and SL legs each covering the full
        position quantity. Re-reads broker truth."""
        self._rebuild_cache()
        naked = []
        for pos in self.cache.positions:
            legs = self._legs(pos["symbol"])
            for role in ("take_profit", "stop_loss"):
                if not any(o["qty"] == pos["qty"] for o in legs[role]):
                    naked.append(pos["symbol"])
                    break
        return CoverageReport(ok=not naked, naked=naked)

    def find_orphaned_orders(self):
        """Protective orders inconsistent with positions: resting against a
        symbol with no position, or sized to a stale quantity. The §5.5/§9
        'orphaned bracket' the kill criteria name."""
        orphans = []
        for order in self.cache.open_orders:
            if order["role"] not in ("take_profit", "stop_loss"):
                continue
            if order["status"] != "open":
                # held children of a still-working (unfilled) parent are not
                # yet resting at the broker — nothing to orphan until the
                # parent fills and they go live
                continue
            pos = self._position(order["symbol"])
            if pos is None or pos["qty"] != order["qty"]:
                orphans.append(order)
        return orphans

    def _protection_prices(self, pos):
        ref = pos["avg_cost"]
        return (round(ref * (1 + self.bracket_spec.take_profit_pct), 2),
                round(ref * (1 - self.bracket_spec.stop_loss_pct), 2))

    def _rebuild_protection(self, symbol):
        """Cancel any stale protective legs on the symbol and attach fresh
        full-coverage TP+SL per the bracket spec. Journaled and flushed."""
        pos = self._position(symbol)
        if pos is None:
            return
        for order in self._legs(symbol)["take_profit"] + self._legs(symbol)["stop_loss"]:
            self.broker.cancel_order(order["order_id"])
            self.journal.append("cancel", order_id=order["order_id"],
                                symbol=symbol, reason="stale_protection")
        tp_px, sl_px = self._protection_prices(pos)
        intent_id = self._next_intent_id("P")
        orders = self.broker.place_protective(
            symbol, "sell", pos["qty"], tp_px, sl_px,
            intent_id=intent_id, sleeve=pos["sleeve"])
        self.journal.append(
            "protective_rebuild", intent_id=intent_id, symbol=symbol,
            qty=pos["qty"], avg_cost=pos["avg_cost"],
            take_profit=orders["take_profit"], stop_loss=orders["stop_loss"],
            bracket_spec={"take_profit_pct": self.bracket_spec.take_profit_pct,
                          "stop_loss_pct": self.bracket_spec.stop_loss_pct})
        self.journal.flush()
        self._rebuild_cache()

    # --- §5.3 envelope state, derived from broker truth ----------------------------

    def trades_today(self):
        """Entries executed or working today, per broker truth — a crash
        cannot reset the overtrading guard by forgetting it."""
        today = self.session_date
        count = sum(1 for e in self.cache.executions
                    if e["role"] == "entry" and e["ts"][:10] == today)
        count += sum(1 for o in self.cache.open_orders
                     if o["role"] == "entry" and o["ts"][:10] == today)
        return count

    def sleeve_exposure(self, sleeve):
        """Positions at cost basis plus live entry notional for the sleeve."""
        total = sum(p["qty"] * p["avg_cost"] for p in self.cache.positions
                    if p["sleeve"] == sleeve)
        total += sum(o["qty"] * o["limit_price"] for o in self.cache.open_orders
                     if o["role"] == "entry" and o["sleeve"] == sleeve)
        return total

    # --- order flow -----------------------------------------------------------------

    def submit(self, intent: env.OrderIntent) -> SubmitResult:
        """The only way an order reaches the broker: envelope clamps first
        (§5.3), then the journaled command/ack/fill sequence (§5.2)."""
        if not self.armed:
            return SubmitResult(False, "-", reasons=("disarmed",))
        intent_id = self._next_intent_id()
        decision = env.clamp(intent, self.clamps,
                             trades_today=self.trades_today(),
                             sleeve_exposure=self.sleeve_exposure(intent.sleeve))
        self.journal.append(
            "command", intent_id=intent_id,
            intent={"symbol": intent.symbol, "side": intent.side,
                    "qty": intent.qty, "limit_price": intent.limit_price,
                    "sleeve": intent.sleeve, "kind": intent.kind,
                    "reason": intent.reason},
            envelope={"max_order_notional": self.clamps.max_order_notional,
                      "max_trades_per_day": self.clamps.max_trades_per_day,
                      "sleeve_cap": self.clamps.sleeve_caps.get(intent.sleeve),
                      "trades_today": self.trades_today(),
                      "sleeve_exposure": round(
                          self.sleeve_exposure(intent.sleeve), 2),
                      "decision": {"allowed": decision.allowed,
                                   "qty": decision.qty,
                                   "reasons": list(decision.reasons)}})
        if not decision.allowed:
            self.journal.append("rejected", intent_id=intent_id,
                                reasons=list(decision.reasons))
            self.journal.flush()
            return SubmitResult(False, intent_id, qty=0, reasons=decision.reasons)

        tp_px = sl_px = None
        if intent.side == "buy":
            ref = intent.limit_price
            tp_px = round(ref * (1 + self.bracket_spec.take_profit_pct), 2)
            sl_px = round(ref * (1 - self.bracket_spec.stop_loss_pct), 2)
        parent = self.broker.place_bracket(
            intent.symbol, intent.side, decision.qty, intent.limit_price,
            tp_px, sl_px, intent_id=intent_id, sleeve=intent.sleeve)
        children = [o for o in self.broker.open_orders()
                    if o["parent_id"] == parent["order_id"]]
        self.journal.append("ack", intent_id=intent_id, order=parent,
                            children=children)
        result = SubmitResult(True, intent_id, qty=decision.qty,
                              reasons=decision.reasons,
                              order_id=parent["order_id"])
        for exc in self.broker.executions():
            if exc["order_id"] == parent["order_id"]:
                self.journal.append("fill", **exc)
                result.exec_id = exc["exec_id"]
        self.journal.flush()

        self._rebuild_cache()
        # Any fill that changes a position's size leaves the resting
        # protection sized to the old quantity. Reconcile in the same action
        # — cancel on full exit, rebuild on resize — so no lane action ever
        # leaves an orphaned or under-sized bracket (§5.6, §9 kill criteria).
        if intent.side == "sell":
            pos = self._position(intent.symbol)
            if pos is None:
                legs = self._legs(intent.symbol)
                for leg in legs["take_profit"] + legs["stop_loss"]:
                    self.broker.cancel_order(leg["order_id"])
                    self.journal.append("cancel", order_id=leg["order_id"],
                                        symbol=intent.symbol,
                                        reason="position_closed")
                self.journal.flush()
                self._rebuild_cache()
            elif not self.verify_coverage().ok:
                self._rebuild_protection(intent.symbol)
        elif not self.verify_coverage().ok:
            self._rebuild_protection(intent.symbol)
        return result

    # --- session --------------------------------------------------------------

    def start_session(self, session_date, ca_calendar=()):
        """Daily open: resync's cache rebuild + gap reconciliation (§5.1),
        then the §5.5 corporate-actions guard against the calendar, then
        resync's generic naked-position repair and re-arm. The CA guard runs
        before the generic repair so a split-desynced bracket gets its
        split-specific cancel/rebuild (with the true pre-split terms in its
        journal trail) instead of being silently absorbed by resync's blunt
        crash-recovery repair first."""
        from execution_lane.ca_guard import CorporateActionsGuard
        resync_report = self._resync_reconcile(session_date)
        ca_report = CorporateActionsGuard(self).run(session_date, ca_calendar)
        self._repair_coverage_and_arm(resync_report)
        coverage = self.verify_coverage()
        return resync_report, ca_report, coverage
