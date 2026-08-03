"""Time-travel test for the point-in-time universe (plan §3, task 3.3).

Core acceptance: pick 3 random past dates (seeded RNG, reproducible) and require
the universe membership reconstructed from the database to match the recorded
source exactly — the recorded source being source/sp100_snapshots.json, parsed
here through an independent code path (not the ingest code).

Also pinned: every snapshot boundary, same-day vandalism collapse, the
UBER/CHTR membership flip window, departed-name retention, symbol_map rename
continuity (BK→BNY verified, HON→HONA inferred), corporate-action semantics
(true splits vs Yahoo spin-off encodings), the plan-mandated schema columns,
and per-series provenance (§3.4).

Fully offline: the database is built in a tmp dir from the committed recorded
sources. Run from the repo root:  python -m pytest tests/test_universe_time_travel.py -v
"""

import json
import random
from datetime import date, timedelta
from pathlib import Path

import pytest

from data_layer import ingest, query

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "data_layer" / "source"
SEED = 20260803  # fixed: the "random" dates are reproducible and reviewable
ALLOWED_ACTIONS = {"split", "dividend", "spinoff", "rename", "delisting"}


@pytest.fixture(scope="session")
def conn(tmp_path_factory):
    db = tmp_path_factory.mktemp("universe") / "universe.db"
    ingest.build(db)
    c = query.connect(db)
    yield c
    c.close()


@pytest.fixture(scope="session")
def recorded():
    return json.loads((SOURCE / "sp100_snapshots.json").read_text())


def source_timeline(recorded):
    """Independent reconstruction of the effective-snapshot timeline straight
    from the recorded source: last revision of a day wins, base snapshot
    clamped to range_start. Mirrors no ingest code on purpose."""
    by_date = {}
    for snap in recorded["snapshots"]:
        by_date[snap["date"]] = snap
    return sorted(
        (max(d, recorded["range_start"]), set(snap["symbols"]))
        for d, snap in by_date.items()
    )


def source_membership(recorded, as_of):
    members = None
    for eff, syms in source_timeline(recorded):
        if eff <= as_of:
            members = syms
        else:
            break
    return members


# --- task 3.3: time travel -------------------------------------------------

def test_random_dates_match_recorded_source(conn, recorded):
    lo, hi = query.coverage(conn)
    assert lo == recorded["range_start"]
    assert hi == recorded["retrieved"]
    rng = random.Random(SEED)
    start, end = date.fromisoformat(lo), date.fromisoformat(hi)
    span = (end - start).days
    picks = sorted(start + timedelta(days=rng.randrange(span + 1)) for _ in range(3))
    print(f"\ntime-travel dates (seed {SEED}): {[p.isoformat() for p in picks]}")
    for p in picks:
        expected = source_membership(recorded, p.isoformat())
        assert expected is not None and len(expected) > 90
        actual = query.membership_as_of(conn, p.isoformat())
        assert actual == expected, f"membership mismatch on {p}"


def test_every_snapshot_boundary_is_exact(conn, recorded):
    timeline = source_timeline(recorded)
    for (prev_eff, prev_syms), (eff, syms) in zip(timeline, timeline[1:]):
        day_before = (date.fromisoformat(eff) - timedelta(days=1)).isoformat()
        if day_before < prev_eff:
            continue  # same-day transition, covered by the last-of-day rule
        assert query.membership_as_of(conn, day_before) == prev_syms, day_before
        assert query.membership_as_of(conn, eff) == syms, eff


def test_same_day_vandalism_never_becomes_effective(conn):
    bad = conn.execute(
        "SELECT COUNT(*) AS n FROM universe_membership "
        "WHERE symbol IN ('PIZZA', 'APPL', 'TJX') AND in_universe = 1").fetchone()["n"]
    assert bad == 0
    assert query.is_member(conn, "AAPL", "2023-12-03")  # wobble day one
    assert query.is_member(conn, "AAPL", "2024-07-23")  # wobble day two


def test_uber_charter_flip_window(conn):
    cases = [  # (date, UBER in?, CHTR in?) — the September 2025 add/revert/re-add
        ("2025-09-04", False, True),
        ("2025-09-06", True, False),
        ("2025-09-08", False, True),
        ("2025-09-22", True, False),
    ]
    for d, uber, chtr in cases:
        assert query.is_member(conn, "UBER", d) is uber, d
        assert query.is_member(conn, "CHTR", d) is chtr, d


def test_departed_names_retained_and_marked(conn):
    for sym, last_day in [("DOW", "2025-03-21"), ("PYPL", "2026-03-21")]:
        assert query.is_member(conn, sym, last_day)
        rows = conn.execute(
            "SELECT COUNT(*) AS n, SUM(in_universe) AS members "
            "FROM universe_membership WHERE symbol = ? AND date > ?",
            (sym, last_day)).fetchone()
        assert rows["n"] > 0, f"{sym}: rows must be retained after departure"
        assert rows["members"] == 0, f"{sym}: must be marked in_universe=0 after departure"


def test_latest_universe_matches_item_zero(conn, recorded):
    item_zero = set(json.loads((ROOT / "item_zero" / "universe.json").read_text())["tickers"])
    assert query.membership_as_of(conn, recorded["retrieved"]) == item_zero


# --- symbol_map: rename continuity ------------------------------------------

def test_bk_bny_verified_rename_continuity(conn):
    assert query.resolve_symbol(conn, "BK", "2026-06-01") == "BNY"
    assert query.resolve_symbol(conn, "BK", "2026-05-21") == "BK"
    assert query.resolve_symbol(conn, "BNY", "2026-05-01") == "BK"
    segments = query.entity_symbols(conn, "BNY")
    assert segments == [("BK", None, "2026-05-21"), ("BNY", "2026-05-22", None)]
    assert query.is_member(conn, "BK", "2026-05-21")
    assert not query.is_member(conn, "BK", "2026-05-22")
    assert query.is_member(conn, "BNY", "2026-05-22")
    assert not query.is_member(conn, "BNY", "2026-05-21")


def test_hon_hona_mapping_is_flagged_inferred(conn):
    row = conn.execute(
        "SELECT * FROM symbol_map WHERE old_symbol = 'HON' AND new_symbol = 'HONA'"
    ).fetchone()
    assert row is not None and row["effective_from"] == "2026-06-29"
    assert "INFERRED" in row["note"].upper()
    assert query.is_member(conn, "HON", "2026-06-28")
    assert query.is_member(conn, "HONA", "2026-06-29")


# --- corporate_actions -------------------------------------------------------

def test_corporate_actions_semantics(conn):
    rows = conn.execute("SELECT * FROM corporate_actions").fetchall()
    assert len(rows) > 1000
    assert {r["action_type"] for r in rows} <= ALLOWED_ACTIONS
    for r in rows:
        terms = json.loads(r["raw_terms"])  # must always be valid JSON
        if r["action_type"] == "split":     # true share-count splits only
            assert r["ratio"] is not None and r["ratio"] >= 2
            assert float(r["ratio"]).is_integer(), r["ex_date"]
        if r["action_type"] == "spinoff" and r["source"] == "yfinance":
            assert "stock_split_field" in terms  # Yahoo encoding preserved
    nvda = [r for r in rows if r["symbol"] == "NVDA" and r["action_type"] == "split"]
    assert [(r["ex_date"], r["ratio"]) for r in nvda] == [("2024-06-10", 10.0)]
    gev = [r for r in rows if r["action_type"] == "spinoff" and r["source"] == "curated"]
    assert len(gev) == 1 and gev[0]["symbol"] == "GE" and gev[0]["ex_date"] == "2024-04-02"
    assert json.loads(gev[0]["raw_terms"])["spawned"] == "GEV"


def test_corporate_actions_keyed_to_historical_symbol(conn):
    bk = query.corporate_actions_between(conn, symbol="BK", start="2024-01-01", end="2024-12-31")
    assert bk and all(r["action_type"] == "dividend" for r in bk)
    assert json.loads(bk[0]["raw_terms"])["source_symbol"] == "BNY"
    bny = query.corporate_actions_between(conn, symbol="BNY", start="2026-05-22", end="2026-12-31")
    assert bny  # post-rename dividends stay under the new symbol


# --- schema + provenance (plan §3 design, §3.4) ------------------------------

def test_schema_matches_plan():
    fresh = query.connect(ingest.DEFAULT_DB) if ingest.DEFAULT_DB.exists() else None
    conn = fresh or query.connect(":memory:")
    if fresh is None:
        conn.executescript(ingest.SCHEMA)
    cols = {t: {r["name"] for r in conn.execute(f"PRAGMA table_info({t})")}
            for t in ("universe_membership", "symbol_map", "corporate_actions")}
    conn.close()
    assert {"date", "symbol", "in_universe"} <= cols["universe_membership"]
    assert {"effective_from", "effective_to", "old_symbol", "new_symbol"} <= cols["symbol_map"]
    assert {"symbol", "ex_date", "action_type", "ratio", "raw_terms"} <= cols["corporate_actions"]


def test_provenance_rows_present(conn):
    for series in ("universe_membership", "symbol_map", "corporate_actions"):
        row = query.provenance(conn, series)
        assert row is not None, series
        assert row["source"] and row["retrieved"] and row["limitations"]
