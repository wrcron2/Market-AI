"""Record point-in-time S&P 100 constituent snapshots from Wikipedia page history (plan §3.2).

The documented free proxy for historical index membership (see source/MANIFEST.md):
every revision of the Wikipedia "S&P 100" page is a dated snapshot of the
constituent table. This script enumerates the revisions covering RANGE_START ..
today, fetches each revision's wikitext via the MediaWiki API, parses the
constituent table, and collapses consecutive identical constituent sets into
snapshots. The result is committed at source/sp100_snapshots.json and is the
*recorded source* the time-travel test reconstructs membership against — the
test suite itself never touches the network.

Ticker normalization: Wikipedia renders Berkshire as "BRK.B"; the canonical
form used by the price layer (Yahoo, item_zero/data/prices.csv) is "BRK-B".
Normalization is uppercase + '.'->'-'. Anything else fails loudly.

Usage: python3 data_layer/fetch_source.py   (network; one-off, re-run to extend)
"""

import json
import re
import time
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
SOURCE = HERE / "source"
API = "https://en.wikipedia.org/w/api.php"
PAGE_TITLE = "S&P 100"
RANGE_START = "2023-10-01"  # covers the 2023-11-01 price-history start with margin
UA = "MarketFlow-data-layer/1.0 (research; contact: repo owner)"
SUSPECT_DRIFT = 5  # snapshot whose size jumps by more than this vs both neighbours is flagged


def api_get(params):
    params = dict(params, format="json")
    url = API + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def list_revisions(today):
    """All revisions (newest-first) in [RANGE_START, today], plus the newest
    revision *before* RANGE_START (the base snapshot effective at range start)."""
    revs = []
    cont = {}
    while True:
        data = api_get({
            "action": "query", "prop": "revisions", "titles": PAGE_TITLE,
            "rvprop": "ids|timestamp|comment", "rvstart": f"{today}T23:59:59Z",
            "rvend": f"{RANGE_START}T00:00:00Z", "rvlimit": "max", **cont,
        })
        page = next(iter(data["query"]["pages"].values()))
        revs.extend(page.get("revisions", []))
        if "continue" not in data:
            break
        cont = data["continue"]
        time.sleep(0.5)
    base = api_get({
        "action": "query", "prop": "revisions", "titles": PAGE_TITLE,
        "rvprop": "ids|timestamp", "rvstart": f"{RANGE_START}T00:00:00Z",
        "rvdir": "older", "rvlimit": "1",
    })
    revs.extend(next(iter(base["query"]["pages"].values())).get("revisions", []))
    revs.sort(key=lambda r: r["timestamp"])
    return revs


_REF = re.compile(r"<ref[^>]*>.*?</ref>|<ref[^>]*/>", re.S)
_COMMENT = re.compile(r"<!--.*?-->", re.S)
_LINK = re.compile(r"\[\[([^\]|]*\|)?([^\]]*)\]\]")


def clean_cell(cell):
    cell = _REF.sub("", cell)
    cell = _COMMENT.sub("", cell)
    cell = _LINK.sub(lambda m: m.group(2), cell)  # [[a|b]] -> b, [[a]] -> a
    return cell.strip()


def normalize_symbol(raw):
    sym = clean_cell(raw).upper().replace(".", "-").replace(" ", "")
    if not re.fullmatch(r"[A-Z0-9-]+", sym):
        raise ValueError(f"unparseable symbol cell: {raw!r}")
    return sym


def split_cells(row):
    """Cells of one wikitext table row; handles one-per-line and !! / || styles."""
    cells = []
    for line in row.splitlines():
        line = line.strip()
        if line.startswith("!"):
            cells.extend(line.lstrip("! ").split(" !! "))
        elif line.startswith("|") and not line.startswith(("|}", "|+")):
            cells.extend(line.lstrip("| ").split(" || "))
    return cells


def parse_constituents(wikitext):
    """Parse the constituents table -> {canonical_symbol: company_name}.

    Picks the wikitext table whose header row mentions Symbol and Name/Security;
    takes the first two cells of each body row. Raises if no such table exists
    so a format drift surfaces instead of silently recording garbage.
    """
    tables = re.findall(r"\{\|.*?\n\|\}", wikitext, re.S)
    for tbl in tables:
        rows = tbl.split("|-")
        header_at = None
        for idx, row in enumerate(rows):
            if any(l.strip().startswith("!") for l in row.splitlines()):
                header_at = idx
                break
        if header_at is None:
            continue
        headers = [clean_cell(h).lower() for h in split_cells(rows[header_at])]
        if not any("symbol" in h or "ticker" in h for h in headers):
            continue
        if not any("name" in h or "security" in h for h in headers):
            continue
        out = {}
        for row in rows[header_at + 1:]:
            cells = split_cells(row)
            if len(cells) < 2:
                continue
            sym = normalize_symbol(cells[0])
            out[sym] = clean_cell(cells[1])
        if out:
            return out
    raise ValueError("constituents table not found / format drifted")


def fetch_batched_contents(revids):
    """revid -> wikitext, fetched 50 ids per API call."""
    contents = {}
    for i in range(0, len(revids), 50):
        chunk = revids[i : i + 50]
        data = api_get({
            "action": "query", "prop": "revisions",
            "revids": "|".join(str(r) for r in chunk),
            "rvprop": "ids|timestamp|content", "rvslots": "main",
        })
        for page in data["query"]["pages"].values():
            for rev in page["revisions"]:
                contents[rev["revid"]] = {
                    "timestamp": rev["timestamp"],
                    "wikitext": rev["slots"]["main"]["*"],
                }
        time.sleep(1.0)
    return contents


def main():
    today = date.today().isoformat()
    revs = list_revisions(today)
    print(f"{len(revs)} revisions to parse "
          f"({revs[0]['timestamp']} .. {revs[-1]['timestamp']})")
    contents = fetch_batched_contents([r["revid"] for r in revs])

    snapshots = []
    prev = None
    for r in revs:
        c = contents[r["revid"]]
        try:
            members = parse_constituents(c["wikitext"])
        except ValueError as exc:
            print(f"WARNING revid {r['revid']} ({c['timestamp']}): {exc} — revision skipped")
            continue
        key = sorted(members)
        if prev is not None and key == prev["symbols"]:
            continue  # identical constituent set — collapse
        snap = {
            "revid": r["revid"],
            "timestamp": c["timestamp"],
            "date": c["timestamp"][:10],
            "symbols": key,
            "names": {s: members[s] for s in key},
            "comment": r.get("comment", "")[:200],
        }
        snapshots.append(snap)
        prev = snap

    # Flag size outliers (vandalism that stood, parser drift) instead of silently
    # trusting them; ingest refuses suspect rows unless reviewed.
    for i, snap in enumerate(snapshots):
        n = len(snap["symbols"])
        neighbours = [len(snapshots[j]["symbols"]) for j in (i - 1, i + 1)
                      if 0 <= j < len(snapshots)]
        if neighbours and all(abs(n - m) > SUSPECT_DRIFT for m in neighbours):
            snap["suspect"] = True
            print(f"SUSPECT snapshot {snap['date']} revid {snap['revid']}: "
                  f"{n} symbols vs neighbours {neighbours}")

    payload = {
        "series": "sp100_constituent_snapshots",
        "source": "Wikipedia 'S&P 100' page revision history (MediaWiki API)",
        "page_url": "https://en.wikipedia.org/wiki/S%26P_100",
        "retrieved": today,
        "range_start": RANGE_START,
        "normalization": "uppercase; '.'->'-' (Wikipedia BRK.B -> canonical BRK-B, Yahoo form)",
        "granularity": "one snapshot per constituent-set change; effective from revision UTC date; last revision of a day wins",
        "snapshots": snapshots,
    }
    SOURCE.mkdir(exist_ok=True)
    out = SOURCE / "sp100_snapshots.json"
    out.write_text(json.dumps(payload, indent=1))
    print(f"wrote {out}: {len(snapshots)} distinct snapshots, "
          f"{len(snapshots[-1]['symbols'])} symbols in latest")


if __name__ == "__main__":
    main()
