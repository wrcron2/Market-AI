"""Knowledge gate — checks staged signals against kimi-learner's memory.

Before a signal is staged for the Green Light gate, this module asks the
kimi-learner knowledge endpoint what the learning system has established
about the ticker: open predictions (dated, graded), related debated notes,
and the ledger's calibration stats (hit rate, Brier).

Advisory only (v1): the verdict is attached to the staged order's reasoning
and the Brain Activity feed. It never blocks and never executes. If the
endpoint is unreachable the pipeline continues with stance "unavailable".

Env:
  KNOWLEDGE_URL — default http://host.docker.internal:3100/api/knowledge
                  (kimi-learner app on the same VM host, port 3100)
"""

import json
import os
import urllib.request
from typing import Any

DEFAULT_URL = "http://host.docker.internal:3100/api/knowledge"
TIMEOUT_S = 5

# Orchestrator consensus direction -> learner prediction direction.
DIRECTION_WORD = {"BUY": "up", "COVER": "up", "SHORT": "down", "SELL": "down"}


def fetch_knowledge(symbol: str, direction: str) -> dict[str, Any]:
    """Query the learner. Never raises — pipeline must survive its absence."""
    base = os.getenv("KNOWLEDGE_URL", DEFAULT_URL).rstrip("/")
    url = f"{base}?ticker={symbol}&direction={direction}"
    try:
        with urllib.request.urlopen(url, timeout=TIMEOUT_S) as resp:
            return json.loads(resp.read())
    except Exception as exc:
        return {"stance": "unavailable", "error": str(exc)[:200],
                "openPredictions": [], "relatedNotes": [], "calibration": {}}


def reasoning_section(k: dict[str, Any]) -> str:
    """Render the [Knowledge] block appended to a staged order's reasoning."""
    if not k or k.get("stance") == "unavailable":
        return "[Knowledge] learner unavailable — staged without knowledge check"
    cal = k.get("calibration", {})
    hit = cal.get("hitRatePct")
    cal_txt = (
        f"ledger calibration: {cal.get('graded', 0)} graded, hit rate {hit}%"
        if hit is not None
        else f"ledger calibration: {cal.get('graded', 0)} graded (unproven yet)"
    )
    lines = [
        f"[Knowledge] stance: {k.get('stance')} for {k.get('ticker')} "
        f"{k.get('direction')} · {cal_txt}"
    ]
    for p in k.get("openPredictions", []):
        lines.append(
            f"  open bet #{p['id']}: {p['direction']} conf {p['statedConfidence']} "
            f"by {p['resolveBy']} — {p['claimSummary']}"
        )
    for n in k.get("relatedNotes", [])[:3]:
        lines.append(
            f"  note #{n['id']} ({n['status']}, conf {n['confidence']}): {n['concept']}"
        )
    return "\n".join(lines)


if __name__ == "__main__":
    # Standalone self-test: KNOWLEDGE_URL=http://localhost:3100/api/knowledge
    for symbol, direction in (("NVDA", "up"), ("SPY", "up"), ("QQQ", "down")):
        result = fetch_knowledge(symbol, direction)
        print(reasoning_section(result))
        print("---")
