"""Broker gateway contract and the offline simulated broker (plan §5.1).

BrokerGateway is the seam between the execution lane and any venue: the live
adapter (a later phase — venue per the repo ADR, not this node) and the
SimulatedBroker below both implement it. The lane only ever reads truth
through this interface; it never trusts local state about positions, orders,
or executions.

SimulatedBroker is the "broker side" for the offline validation track. Its
state persists in its own JSON file, written on every mutation — that models
the essential property of a real broker: its state survives our process
dying. The chaos test (tests/test_chaos_resync.py) kills the lane mid-trade
and the broker file is what survives.

Model choices, kept deliberately small:
- Long-only, whole shares (the v2.4 sleeves are long-only per the pinned
  specs). Shorts and fractional shares are rejected.
- Entry brackets are server-side: parent entry order plus attached
  take-profit / stop-loss children. Children are 'held' until the parent
  fills, then 'open' (live). Cancelling a parent cancels its children.
- A marketable limit parent fills immediately at its limit price when
  fill=True (the default); fill=False leaves the parent working.
- apply_split adjusts positions only and deliberately leaves resting orders
  untouched — real venues do not rewrite your brackets for you, which is
  exactly why the §5.5 corporate-actions guard exists.
"""

from __future__ import annotations

import abc
import copy
import json
import os
from datetime import datetime, timezone
from pathlib import Path


def utc_now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class BrokerGateway(abc.ABC):
    """The contract the lane reads truth through. All list-returning methods
    return plain dicts so the lane's cache is venue-neutral."""

    @abc.abstractmethod
    def connect(self): ...

    @abc.abstractmethod
    def disconnect(self): ...

    @abc.abstractmethod
    def is_connected(self) -> bool: ...

    @abc.abstractmethod
    def place_bracket(self, symbol, side, qty, limit_price, take_profit,
                      stop_loss, *, intent_id, sleeve, fill=True) -> dict:
        """Place a parent order with attached TP/SL children. Returns the
        parent order dict. When the parent fills, an execution is recorded
        and the children go live."""

    @abc.abstractmethod
    def place_protective(self, symbol, side, qty, take_profit, stop_loss,
                         *, intent_id, sleeve) -> dict:
        """Attach fresh TP+SL protection to an existing position (no parent).
        Returns {'take_profit': order, 'stop_loss': order}."""

    @abc.abstractmethod
    def cancel_order(self, order_id) -> bool:
        """Cancel an open/held order (cancelling a parent cascades to its
        children). Idempotent: False only if the order id is unknown."""

    @abc.abstractmethod
    def positions(self) -> list: ...

    @abc.abstractmethod
    def open_orders(self) -> list:
        """Status in {'open', 'held'} — everything resting at the broker."""

    @abc.abstractmethod
    def executions(self) -> list: ...

    @abc.abstractmethod
    def known_intent_ids(self) -> set:
        """Every intent_id the broker has ever seen (any order status). The
        resync protocol uses it to tell 'intent never placed' apart from
        'placed, but the ack never reached the journal'."""


class SimulatedBroker(BrokerGateway):
    def __init__(self, state_path, clock=utc_now_iso):
        self._path = Path(state_path)
        self._clock = clock
        self._connected = False
        if self._path.exists():
            self._state = json.loads(self._path.read_text())
        else:
            self._state = {"next_order_seq": 1, "next_exec_seq": 1,
                           "orders": {}, "executions": [], "positions": {}}

    # --- connection ------------------------------------------------------

    def connect(self):
        self._connected = True

    def disconnect(self):
        self._connected = False

    def is_connected(self):
        return self._connected

    def _require_connection(self):
        if not self._connected:
            raise ConnectionError("broker is not connected")

    # --- persistence (broker-side durability is immediate) ----------------

    def _persist(self):
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(json.dumps(self._state, indent=1, sort_keys=True))
        os.replace(tmp, self._path)

    # --- order placement ---------------------------------------------------

    def _new_order(self, role, symbol, side, qty, limit_price, *,
                   intent_id, sleeve, parent_id, status):
        seq = self._state["next_order_seq"]
        self._state["next_order_seq"] = seq + 1
        order = {
            "order_id": f"O{seq:06d}",
            "intent_id": intent_id,
            "symbol": symbol,
            "side": side,
            "qty": int(qty),
            "limit_price": float(limit_price),
            "role": role,                    # entry | exit | take_profit | stop_loss
            "parent_id": parent_id,
            "status": status,                # open | held | filled | cancelled
            "sleeve": sleeve,
            "ts": self._clock(),
        }
        self._state["orders"][order["order_id"]] = order
        return order

    def place_bracket(self, symbol, side, qty, limit_price, take_profit,
                      stop_loss, *, intent_id, sleeve, fill=True):
        self._require_connection()
        if side not in ("buy", "sell"):
            raise ValueError("side must be 'buy' or 'sell'")
        if int(qty) <= 0:
            raise ValueError("qty must be a positive whole number of shares")
        # entries are buys with attached protection; exits are plain sells
        role = "entry" if side == "buy" else "exit"
        parent = self._new_order(
            role, symbol, side, qty, limit_price, intent_id=intent_id,
            sleeve=sleeve, parent_id=None, status="open")
        if side == "buy":
            for child_role, px in (("take_profit", take_profit), ("stop_loss", stop_loss)):
                if px is not None:
                    self._new_order(child_role, symbol, "sell", qty, px,
                                    intent_id=intent_id, sleeve=sleeve,
                                    parent_id=parent["order_id"], status="held")
        if fill:
            self._fill(parent, limit_price)
        self._persist()
        return copy.deepcopy(parent)

    def place_protective(self, symbol, side, qty, take_profit, stop_loss,
                         *, intent_id, sleeve):
        self._require_connection()
        pos = self._state["positions"].get(symbol)
        if not pos or pos["qty"] <= 0:
            raise ValueError(f"no position in {symbol} to protect")
        if int(qty) != pos["qty"]:
            raise ValueError(f"protective qty {qty} != position qty {pos['qty']}")
        tp = self._new_order("take_profit", symbol, side, qty, take_profit,
                             intent_id=intent_id, sleeve=sleeve,
                             parent_id=None, status="open")
        sl = self._new_order("stop_loss", symbol, side, qty, stop_loss,
                             intent_id=intent_id, sleeve=sleeve,
                             parent_id=None, status="open")
        self._persist()
        return {"take_profit": copy.deepcopy(tp), "stop_loss": copy.deepcopy(sl)}

    def _fill(self, order, price):
        order["status"] = "filled"
        seq = self._state["next_exec_seq"]
        self._state["next_exec_seq"] = seq + 1
        self._state["executions"].append({
            "exec_id": f"E{seq:06d}",
            "order_id": order["order_id"],
            "intent_id": order["intent_id"],
            "symbol": order["symbol"],
            "side": order["side"],
            "qty": order["qty"],
            "price": float(price),
            "role": order["role"],
            "sleeve": order["sleeve"],
            "ts": self._clock(),
        })
        # activate the bracket children of a filled parent
        for other in self._state["orders"].values():
            if other["parent_id"] == order["order_id"] and other["status"] == "held":
                other["status"] = "open"
        # update the position (long-only)
        pos = self._state["positions"].setdefault(
            order["symbol"], {"symbol": order["symbol"], "qty": 0,
                              "avg_cost": 0.0, "sleeve": order["sleeve"]})
        if order["side"] == "buy":
            new_qty = pos["qty"] + order["qty"]
            pos["avg_cost"] = round(
                (pos["qty"] * pos["avg_cost"] + order["qty"] * float(price)) / new_qty, 4)
            pos["qty"] = new_qty
            pos["sleeve"] = order["sleeve"]
        else:
            pos["qty"] -= order["qty"]
            if pos["qty"] < 0:
                raise ValueError("sell would take the position negative (long-only)")
            if pos["qty"] == 0:
                del self._state["positions"][order["symbol"]]

    def cancel_order(self, order_id):
        self._require_connection()
        order = self._state["orders"].get(order_id)
        if order is None:
            return False
        if order["status"] in ("open", "held"):
            order["status"] = "cancelled"
            for child in self._state["orders"].values():
                if child["parent_id"] == order_id and child["status"] in ("open", "held"):
                    child["status"] = "cancelled"
        self._persist()
        return True

    # --- truth reads ---------------------------------------------------------

    def positions(self):
        self._require_connection()
        return [copy.deepcopy(self._state["positions"][s])
                for s in sorted(self._state["positions"])]

    def open_orders(self):
        self._require_connection()
        out = [o for o in self._state["orders"].values() if o["status"] in ("open", "held")]
        out.sort(key=lambda o: o["order_id"])
        return copy.deepcopy(out)

    def executions(self):
        self._require_connection()
        return copy.deepcopy(self._state["executions"])

    def known_intent_ids(self):
        self._require_connection()
        return {o["intent_id"] for o in self._state["orders"].values()}

    # --- simulation controls (NOT part of the gateway contract) --------------

    def apply_split(self, symbol, ratio, ex_date):
        """Adjust positions for a split; resting orders are deliberately left
        untouched (venues do not rewrite your brackets). Cash-in-lieu for
        fractional results is out of scope — the ratio must divide every
        position in the symbol exactly."""
        pos = self._state["positions"].get(symbol)
        if pos:
            new_qty = pos["qty"] * ratio
            if int(new_qty) != new_qty:
                raise ValueError(f"split {ratio} of {pos['qty']} shares is fractional")
            pos["qty"] = int(new_qty)
            pos["avg_cost"] = round(pos["avg_cost"] / ratio, 4)
        self._persist()
