"""Record corporate actions (splits, dividends) for every symbol that appears in an
*effective* S&P 100 snapshot (plan §3.2, corporate_actions table source).

Source: Yahoo Finance via yfinance (same free proxy as the price layer), window
[RANGE_START, today]. Output is committed at source/corporate_actions.csv and is
the recorded source for ingestion — tests never touch the network.

Spin-offs / renames / delistings are NOT reliably exposed by yfinance; those are
curated separately in source/corporate_actions_curated.csv with references, and
the gap is stated in source/MANIFEST.md.

Usage: python3 data_layer/fetch_corporate_actions.py   (network; ~2 min)
"""

import csv
import json
import time
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "source"
RANGE_START = "2023-10-01"
COLUMNS = ["symbol", "ex_date", "action_type", "ratio", "raw_terms", "source", "retrieved"]


def effective_symbols():
    """Union of symbols over effective snapshots (last revision of each day wins)."""
    payload = json.loads((SOURCE / "sp100_snapshots.json").read_text())
    by_date = {}
    for snap in payload["snapshots"]:
        by_date[snap["date"]] = snap  # snapshots are chronological; later overwrite
    symbols = set()
    for snap in by_date.values():
        symbols.update(snap["symbols"])
    return sorted(symbols)


def main():
    import yfinance as yf  # imported here so the offline modules stay stdlib-only

    today = date.today().isoformat()
    symbols = effective_symbols()
    print(f"{len(symbols)} symbols to fetch, window {RANGE_START}..{today}")

    rows, failed = [], []
    for sym in symbols:
        for attempt in (0, 1):
            try:
                actions = yf.Ticker(sym).actions
                if actions is not None and not actions.empty:
                    for ts, r in actions.iterrows():
                        ex = ts.date().isoformat()
                        if ex < RANGE_START or ex > today:
                            continue
                        div = float(r.get("Dividends", 0.0) or 0.0)
                        split = float(r.get("Stock Splits", 0.0) or 0.0)
                        if div > 0:
                            rows.append([sym, ex, "dividend", "",
                                         json.dumps({"amount": div, "currency": "USD"}),
                                         "yfinance", today])
                        if split > 0:
                            # Yahoo encodes spin-offs in the Stock Splits field as a
                            # price-adjustment factor (GE 1.253 at the GEV spin). A true
                            # split has an integer ratio >= 2; anything else is recorded
                            # as a spin-off event with the factor preserved, so 'split'
                            # rows always mean a real share-count change.
                            if split >= 2 and float(split).is_integer():
                                action_type, note = "split", None
                            else:
                                action_type = "spinoff"
                                note = ("Yahoo spin-off encoding (non-integer or <2 factor); "
                                        "spawned entity and terms require curation")
                            terms = {"stock_split_field": split}
                            if note:
                                terms["classification_note"] = note
                            rows.append([sym, ex, action_type, repr(split),
                                         json.dumps(terms), "yfinance", today])
                break
            except Exception as exc:  # noqa: BLE001 — record and continue, reported below
                if attempt == 1:
                    print(f"FAILED {sym}: {exc}")
                    failed.append(sym)
                else:
                    time.sleep(5.0)
        time.sleep(0.7)

    rows.sort()
    SOURCE.mkdir(exist_ok=True)
    out = SOURCE / "corporate_actions.csv"
    with out.open("w", newline="") as fh:
        csv.writer(fh).writerows([COLUMNS] + rows)

    report = {"retrieved": today, "range_start": RANGE_START,
              "symbols_requested": len(symbols), "symbols_failed": sorted(failed),
              "rows": len(rows)}
    (SOURCE / "corporate_actions_fetch_report.json").write_text(json.dumps(report, indent=2))
    print(f"wrote {out}: {len(rows)} rows; failed: {failed or 'none'}")


if __name__ == "__main__":
    main()
