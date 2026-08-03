"""Corporate-actions guard (plan §5.5).

Runs before session start, after the §5.1 resync: reconcile open brackets
against the corporate-actions calendar and cancel/rebuild around ex-dates.

Why it exists: a split re-prices the position overnight (2:1 turns 10 @ $120
into 20 @ $60) but no venue rewrites your resting brackets — a stop placed
pre-split is a stale order on a position that no longer exists in that form,
i.e. an orphaned bracket the moment the ex-date opens. The §9 kill criteria
halt the sleeve on any orphaned bracket after a corporate action, so the
guard is the scheduled job that prevents exactly that.

Rule: for every SPLIT with ex_date <= the session date that the guard has
not already handled, on every symbol the system has exposure to (position or
open orders):
  1. cancel every open order on the symbol (pre-adjustment terms — stale);
  2. rebuild full-coverage brackets from the post-adjustment position
     (delegated to the lane's §5.1 protection rebuilder);
  3. journal `ca_handled` and flush, so a crash mid-guard re-runs the CA
     safely (cancellation is idempotent at the broker) and a restart never
     double-processes.

Splits are the only type that re-terms orders; other action types
(dividend / spinoff / rename / delisting per the §3 schema) are reported as
informational for the operator and left for their own handling.

The calendar is any iterable of mappings with keys
(symbol, ex_date, action_type, ratio) — the shape the §3 data layer's
corporate_actions table serves (data_layer.query.corporate_actions_between),
so no conversion layer is needed when the two are wired together.
"""

from __future__ import annotations


class CorporateActionsGuard:
    def __init__(self, lane):
        self.lane = lane

    @staticmethod
    def _key(ca):
        return f"{ca['symbol']}|{ca['ex_date']}|{ca['action_type']}"

    def run(self, session_date, calendar) -> dict:
        lane = self.lane
        handled = {e["ca_key"] for e in lane.journal.durable_entries()
                   if e["kind"] == "ca_handled"}
        report = {"session_date": session_date, "handled": [],
                  "informational": [], "cancelled": [], "rebuilt": []}

        for ca in sorted(calendar, key=lambda c: (c["ex_date"], c["symbol"])):
            key = self._key(ca)
            if ca["ex_date"] > session_date or key in handled:
                continue
            symbol = ca["symbol"]
            if ca["action_type"] != "split":
                report["informational"].append(
                    {"symbol": symbol, "ex_date": ca["ex_date"],
                     "action_type": ca["action_type"],
                     "note": "non-split action: no bracket re-terming; "
                             "left for operator review"})
                continue

            exposure = (lane._position(symbol) is not None
                        or any(o["symbol"] == symbol for o in lane.cache.open_orders))
            if not exposure:
                lane.journal.append("ca_handled", ca_key=key, symbol=symbol,
                                    ex_date=ca["ex_date"], ratio=ca.get("ratio"),
                                    action="none", note="no exposure")
                lane.journal.flush()
                handled.add(key)
                report["handled"].append(key)
                continue

            # 1. cancel every open order on the symbol — all pre-split terms
            cancelled_here = []
            for order in [o for o in lane.cache.open_orders if o["symbol"] == symbol]:
                lane.broker.cancel_order(order["order_id"])
                lane.journal.append(
                    "ca_cancel", ca_key=key, order_id=order["order_id"],
                    symbol=symbol, stale_terms={"qty": order["qty"],
                                                "limit_price": order["limit_price"],
                                                "role": order["role"]})
                cancelled_here.append(order["order_id"])
            report["cancelled"].extend(cancelled_here)

            # 2. rebuild full-coverage brackets on the post-split position
            lane._rebuild_cache()
            rebuilt = []
            if lane._position(symbol) is not None:
                pre = {o["order_id"] for o in lane.cache.open_orders}
                lane._rebuild_protection(symbol)
                rebuilt = [o["order_id"] for o in lane.cache.open_orders
                           if o["symbol"] == symbol and o["order_id"] not in pre]
                report["rebuilt"].extend(rebuilt)

            # 3. mark handled + flush: a crash after this point is clean
            lane.journal.append("ca_handled", ca_key=key, symbol=symbol,
                                ex_date=ca["ex_date"], ratio=ca.get("ratio"),
                                action="cancel_rebuild",
                                cancelled=cancelled_here, rebuilt=rebuilt)
            lane.journal.flush()
            handled.add(key)
            report["handled"].append(key)

        return report
