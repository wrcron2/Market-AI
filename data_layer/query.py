"""Time-travel query API over the point-in-time universe database (plan §3.3).

All functions are pure lookups — no mutation, no network. The fortress and the
time-travel test both consume this module; the test additionally reconstructs
membership directly from source/sp100_snapshots.json (an independent code path)
and requires exact agreement.
"""

import sqlite3
from pathlib import Path

DEFAULT_DB = Path(__file__).resolve().parent / "universe.db"


def connect(db_path=DEFAULT_DB):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def coverage(conn):
    """(min_date, max_date) membership coverage."""
    row = conn.execute("SELECT MIN(date) AS lo, MAX(date) AS hi FROM universe_membership").fetchone()
    return row["lo"], row["hi"]


def membership_as_of(conn, as_of_date):
    """Exact universe membership (set of canonical symbols) on a calendar date.

    Empty set means the date is outside recorded coverage — callers must check
    coverage() rather than treat empty as a real empty universe.
    """
    rows = conn.execute(
        "SELECT symbol FROM universe_membership WHERE date = ? AND in_universe = 1",
        (as_of_date,)).fetchall()
    return {r["symbol"] for r in rows}


def is_member(conn, symbol, as_of_date):
    row = conn.execute(
        "SELECT in_universe FROM universe_membership WHERE date = ? AND symbol = ?",
        (as_of_date, symbol)).fetchone()
    return bool(row and row["in_universe"])


def resolve_symbol(conn, symbol, as_of_date):
    """The ticker under which the entity was listed on as_of_date.

    Walks symbol_map in both directions: if `symbol` was renamed before
    as_of_date, returns its successor; if `symbol` only came into existence
    after as_of_date, returns the predecessor. Identity when no mapping applies.
    """
    seen = set()
    current = symbol
    while current not in seen:
        seen.add(current)
        fwd = conn.execute(
            "SELECT new_symbol FROM symbol_map WHERE old_symbol = ? "
            "AND effective_from <= ? ORDER BY effective_from LIMIT 1",
            (current, as_of_date)).fetchone()
        if fwd:
            current = fwd["new_symbol"]
            continue
        bwd = conn.execute(
            "SELECT old_symbol FROM symbol_map WHERE new_symbol = ? "
            "AND effective_from > ? ORDER BY effective_from DESC LIMIT 1",
            (current, as_of_date)).fetchone()
        if bwd:
            current = bwd["old_symbol"]
            continue
    return current


def entity_symbols(conn, symbol):
    """Ordered [(symbol, valid_from, valid_to)] segments for the whole rename
    chain containing `symbol`; valid_from/valid_to are None at the open ends.
    valid_to is the day before the next segment's effective_from."""
    # walk to the oldest ancestor
    first, seen = symbol, set()
    while first not in seen:
        seen.add(first)
        row = conn.execute(
            "SELECT old_symbol FROM symbol_map WHERE new_symbol = ?", (first,)).fetchone()
        if not row:
            break
        first = row["old_symbol"]
    # walk forward collecting segments
    segments, current, prev_from = [], first, None
    while True:
        row = conn.execute(
            "SELECT new_symbol, effective_from FROM symbol_map WHERE old_symbol = ? "
            "ORDER BY effective_from LIMIT 1", (current,)).fetchone()
        if not row:
            segments.append((current, prev_from, None))
            break
        segments.append((current, prev_from, _day_before(row["effective_from"])))
        current, prev_from = row["new_symbol"], row["effective_from"]
    return segments


def corporate_actions_between(conn, symbol=None, start=None, end=None, action_type=None):
    """Corporate actions filtered by symbol / ex_date range / type."""
    sql, params = "SELECT * FROM corporate_actions WHERE 1=1", []
    if symbol is not None:
        sql += " AND symbol = ?"
        params.append(symbol)
    if start is not None:
        sql += " AND ex_date >= ?"
        params.append(start)
    if end is not None:
        sql += " AND ex_date <= ?"
        params.append(end)
    if action_type is not None:
        sql += " AND action_type = ?"
        params.append(action_type)
    return conn.execute(sql + " ORDER BY ex_date, symbol", params).fetchall()


def provenance(conn, series):
    return conn.execute("SELECT * FROM provenance WHERE series = ?", (series,)).fetchone()


def _day_before(iso_date):
    from datetime import date, timedelta
    return (date.fromisoformat(iso_date) - timedelta(days=1)).isoformat()
