"""Build the point-in-time universe database from the recorded sources (plan §3.2).

Offline: reads only committed files under data_layer/source/ and (re)builds a
SQLite database with the three Section-3 tables plus a provenance table:

  universe_membership(date, symbol, in_universe)   — one row per calendar date x
      every symbol ever seen in an effective snapshot; departed names keep rows
      marked in_universe=0 (retained and marked, never deleted).
  symbol_map(effective_from, effective_to, old_symbol, new_symbol) — ticker
      changes, so a renamed company stays one continuous entity.
  corporate_actions(symbol, ex_date, action_type, ratio, raw_terms) — splits and
      dividends from yfinance; spin-offs/renames/delistings from curated rows.
      Rows are re-keyed to the symbol valid on ex_date per symbol_map (e.g.
      yfinance serves BNY Mellon's whole history under BNY; pre-rename rows
      become BK rows, and raw_terms.source_symbol preserves the source form).
  provenance(series, source, retrieved, as_of_start, as_of_end, limitations) —
      per-series provenance (plan §3.4); the fortress refuses series without it.

Adjustment-policy boundary (plan §3): signals consume split-adjusted prices,
execution simulation consumes raw prices; corporate_actions.ratio carries the
split factor and raw_terms the exact source row so both adjustments can be
derived. The boundary is drawn here in the schema, not left to convention.

Usage: python3 data_layer/ingest.py [db_path]   (default data_layer/universe.db)
"""

import csv
import json
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "source"
DEFAULT_DB = HERE / "universe.db"

SCHEMA = """
CREATE TABLE universe_membership (
  date         TEXT NOT NULL,             -- YYYY-MM-DD calendar date
  symbol       TEXT NOT NULL,             -- canonical (Yahoo-form) ticker
  in_universe  INTEGER NOT NULL CHECK (in_universe IN (0, 1)),
  source_revid INTEGER NOT NULL,          -- Wikipedia revision this row derives from
  PRIMARY KEY (date, symbol)
);
CREATE INDEX idx_membership_symbol ON universe_membership (symbol, date);

CREATE TABLE symbol_map (
  old_symbol     TEXT NOT NULL,
  new_symbol     TEXT NOT NULL,
  effective_from TEXT NOT NULL,           -- first date new_symbol is the valid form
  effective_to   TEXT,                    -- last date new_symbol is valid; NULL = current
  source         TEXT NOT NULL,
  note           TEXT,
  PRIMARY KEY (old_symbol, effective_from)
);

CREATE TABLE corporate_actions (
  symbol      TEXT NOT NULL,              -- symbol as of ex_date (pre-rename form)
  ex_date     TEXT NOT NULL,
  action_type TEXT NOT NULL CHECK (action_type IN
                ('split', 'dividend', 'spinoff', 'rename', 'delisting')),
  ratio       REAL,                       -- split factor (new shares per old share)
  raw_terms   TEXT NOT NULL,              -- JSON: exact source row / curated terms
  source      TEXT NOT NULL,              -- 'yfinance' | 'curated'
  retrieved   TEXT NOT NULL,
  PRIMARY KEY (symbol, ex_date, action_type, source)
);
CREATE INDEX idx_ca_symbol ON corporate_actions (symbol, ex_date);

CREATE TABLE provenance (
  series      TEXT PRIMARY KEY,
  source      TEXT NOT NULL,
  retrieved   TEXT NOT NULL,
  as_of_start TEXT,
  as_of_end   TEXT,
  limitations TEXT NOT NULL
);
"""

LIMITATIONS = {
    "universe_membership": (
        "Free proxy: Wikipedia 'S&P 100' revision snapshots; index edits may lag real "
        "index changes (page carried an {{update inline}} staleness flag at retrieval); "
        "same-day vandalism collapsed by last-revision-of-day rule; index removals are "
        "membership events, not exchange delistings. See source/MANIFEST.md."
    ),
    "symbol_map": (
        "Curated from snapshot transitions; BK->BNY is a verified ticker-only rename; "
        "HON->HONA is inferred index-continuity (likely a separation — Yahoo lists HONA "
        "with short history); verify before stitching price series. See source/MANIFEST.md."
    ),
    "corporate_actions": (
        "Splits/dividends complete per yfinance; spin-offs/renames/delistings are "
        "curated best-effort and their terms (e.g. GEV distribution ratio) are "
        "unverified against filings. See source/MANIFEST.md."
    ),
}


def effective_snapshots(payload):
    """Chronological [(eff_date, revid, symbols)] with last-revision-of-day winning
    and the base snapshot clamped to range_start."""
    by_date = {}
    for snap in payload["snapshots"]:  # chronological on input
        if snap.get("suspect"):
            raise ValueError(f"suspect snapshot {snap['date']} revid {snap['revid']} "
                             "must be reviewed before ingest")
        by_date[snap["date"]] = snap
    out = []
    for d in sorted(by_date):
        snap = by_date[d]
        out.append((max(d, payload["range_start"]), snap["revid"], set(snap["symbols"])))
    return out


def build(db_path=DEFAULT_DB, source_dir=SOURCE):
    source_dir = Path(source_dir)
    payload = json.loads((source_dir / "sp100_snapshots.json").read_text())
    snapshots = effective_snapshots(payload)
    range_start = payload["range_start"]
    retrieved = payload["retrieved"]
    all_symbols = sorted({s for _, _, syms in snapshots for s in syms})

    db_path = Path(db_path)
    if db_path.exists():
        db_path.unlink()
    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)

        # --- universe_membership: every calendar date x every symbol ever seen
        rows = []
        d = date.fromisoformat(range_start)
        end = date.fromisoformat(retrieved)
        i = 0
        one_day = timedelta(days=1)
        while d <= end:
            while i + 1 < len(snapshots) and snapshots[i + 1][0] <= d.isoformat():
                i += 1
            eff_date, revid, members = snapshots[i]
            ds = d.isoformat()
            rows.extend((ds, sym, 1 if sym in members else 0, revid) for sym in all_symbols)
            d += one_day
        conn.executemany(
            "INSERT INTO universe_membership (date, symbol, in_universe, source_revid) "
            "VALUES (?, ?, ?, ?)", rows)

        # --- symbol_map
        renames = []
        with (source_dir / "symbol_map.csv").open() as fh:
            for r in csv.DictReader(fh):
                renames.append(r)
                conn.execute(
                    "INSERT INTO symbol_map (old_symbol, new_symbol, effective_from, "
                    "effective_to, source, note) VALUES (?, ?, ?, ?, ?, ?)",
                    (r["old_symbol"], r["new_symbol"], r["effective_from"],
                     r["effective_to"] or None, r["source"], r.get("note") or None))

        # --- corporate_actions (machine-fetched + curated), re-keyed to the
        # symbol valid on ex_date per symbol_map (see module docstring)
        predecessor = {m["new_symbol"]: (m["old_symbol"], m["effective_from"])
                       for m in renames}

        def historical_symbol(sym, ex_date):
            while sym in predecessor and ex_date < predecessor[sym][1]:
                sym = predecessor[sym][0]
            return sym

        ca_files = ["corporate_actions.csv", "corporate_actions_curated.csv"]
        for name in ca_files:
            with (source_dir / name).open() as fh:
                for r in csv.DictReader(fh):
                    sym = historical_symbol(r["symbol"], r["ex_date"])
                    raw = r["raw_terms"]
                    if sym != r["symbol"]:
                        terms = json.loads(raw)
                        terms["source_symbol"] = r["symbol"]
                        raw = json.dumps(terms)
                    conn.execute(
                        "INSERT INTO corporate_actions (symbol, ex_date, action_type, "
                        "ratio, raw_terms, source, retrieved) VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (sym, r["ex_date"], r["action_type"],
                         float(r["ratio"]) if r["ratio"] else None,
                         raw, r["source"], r["retrieved"]))

        # --- provenance (plan §3.4)
        conn.executemany(
            "INSERT INTO provenance (series, source, retrieved, as_of_start, as_of_end, "
            "limitations) VALUES (?, ?, ?, ?, ?, ?)",
            [
                ("universe_membership",
                 f"{payload['source']} ({payload['page_url']})",
                 retrieved, range_start, retrieved, LIMITATIONS["universe_membership"]),
                ("symbol_map",
                 "curated from Wikipedia snapshot transitions (source/symbol_map.csv)",
                 retrieved, None, None, LIMITATIONS["symbol_map"]),
                ("corporate_actions",
                 "yfinance Ticker.actions + curated rows "
                 "(source/corporate_actions{,_curated}.csv)",
                 retrieved, range_start, retrieved, LIMITATIONS["corporate_actions"]),
            ])
        conn.commit()

        stats = {
            "membership_rows": conn.execute("SELECT COUNT(*) FROM universe_membership").fetchone()[0],
            "symbols": len(all_symbols),
            "snapshots": len(snapshots),
            "symbol_map_rows": conn.execute("SELECT COUNT(*) FROM symbol_map").fetchone()[0],
            "corporate_action_rows": conn.execute("SELECT COUNT(*) FROM corporate_actions").fetchone()[0],
        }
        return stats
    finally:
        conn.close()


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_DB
    print(json.dumps(build(path), indent=2))
