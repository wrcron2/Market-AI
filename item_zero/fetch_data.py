"""Fetch and cache daily bars for the Item Zero universe (research-grade, plan §1).

Source: Yahoo Finance via yfinance (free proxy, retrieval date stamped in the
fetch report). Raw Open/Close and split-and-dividend-adjusted Close are all
kept: signals/returns use adjusted prices, share counts/price band/notionals
use raw prices (the plan §3 adjustment boundary). Cached to a long CSV so the
expectancy run is reproducible without re-hitting the network.

Usage: python3 fetch_data.py   (from the item_zero directory)
"""

import json
import time
from pathlib import Path

import yfinance as yf

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
FETCH_START = "2023-11-01"   # runway for the 90-trading-day signal lookback
FETCH_END = "2026-08-02"     # exclusive; window under test ends 2026-07-31
CHUNK = 25


def main() -> None:
    DATA.mkdir(exist_ok=True)
    universe = json.loads((HERE / "universe.json").read_text())["tickers"]
    frames = []
    failed = []
    for i in range(0, len(universe), CHUNK):
        chunk = universe[i : i + CHUNK]
        df = yf.download(
            " ".join(chunk),
            start=FETCH_START,
            end=FETCH_END,
            auto_adjust=False,
            progress=False,
            group_by="column",
            threads=False,
        )
        for t in chunk:
            try:
                sub = df.xs(t, level="Ticker", axis=1) if len(chunk) > 1 else df
                sub = sub[["Open", "Close", "Adj Close"]].dropna(how="all")
                if sub.empty:
                    failed.append(t)
                    continue
                sub = sub.reset_index()
                sub.columns = ["date", "open", "close", "adj_close"]
                sub["ticker"] = t
                frames.append(sub)
            except Exception:
                failed.append(t)
        time.sleep(1.0)  # be polite to the free endpoint

    import pandas as pd

    out = pd.concat(frames, ignore_index=True)
    out["date"] = pd.to_datetime(out["date"]).dt.strftime("%Y-%m-%d")
    out = out[["date", "ticker", "open", "close", "adj_close"]]
    out.to_csv(DATA / "prices.csv", index=False)

    report = {
        "retrieved": "2026-08-03",
        "fetch_start": FETCH_START,
        "fetch_end": FETCH_END,
        "tickers_requested": len(universe),
        "tickers_loaded": sorted(out["ticker"].unique()),
        "tickers_failed": sorted(failed),
        "rows": len(out),
    }
    (DATA / "fetch_report.json").write_text(json.dumps(report, indent=2))
    print(f"loaded {len(report['tickers_loaded'])}/{len(universe)} tickers, {len(out)} rows")
    if failed:
        print("failed:", ", ".join(sorted(failed)))


if __name__ == "__main__":
    main()
