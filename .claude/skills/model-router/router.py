#!/usr/bin/env python3
"""
model-router — cost-optimized Claude model dispatch for MarketFlow AI.

Decomposes a task into subtasks on claude-haiku-4-5 (the cheapest model),
classifies each on two axes (complexity x reversibility), routes every
subtask to the cheapest capable model tier, optionally dispatches each
subtask via the Anthropic API, escalates one tier on low-confidence or
failed output (max one escalation, then human review — Green Light
philosophy), and logs token usage + cost per subtask to the project's
SQL database (model_routing_log table; DDL is Supabase/Postgres-ready).

Usage:
  python3 router.py "Add a Sharpe-ratio card to the dashboard"          # plan only
  python3 router.py "..." --execute                                     # plan + dispatch
  python3 router.py "..." --db infra/db/marketflow.db --max-subtasks 6

Requires: pip install anthropic ; ANTHROPIC_API_KEY in env (or an
`ant auth login` profile — the SDK resolves credentials itself).
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sqlite3
import sys
import time
from dataclasses import dataclass, field

import anthropic

# ─── Model tiers (IDs + pricing verified against the claude-api reference,
#     2026-07-04) ──────────────────────────────────────────────────────────────

HAIKU = "claude-haiku-4-5"
SONNET = "claude-sonnet-5"
FABLE = "claude-fable-5"

TIER_ORDER = [HAIKU, SONNET, FABLE]  # escalation goes left → right

SONNET5_INTRO_ENDS = dt.date(2026, 8, 31)


def price_per_mtok(model: str) -> tuple[float, float]:
    """(input, output) USD per million tokens."""
    if model == HAIKU:
        return (1.00, 5.00)
    if model == SONNET:
        # $2/$10 introductory through 2026-08-31, then $3/$15.
        if dt.date.today() <= SONNET5_INTRO_ENDS:
            return (2.00, 10.00)
        return (3.00, 15.00)
    if model == FABLE:
        return (10.00, 50.00)
    raise ValueError(f"unknown model {model}")


COST_TIER = {HAIKU: "$", SONNET: "$$", FABLE: "$$$"}

# Output caps. Sonnet 5 thinking (adaptive, on by default) counts toward
# max_tokens, so it gets extra headroom. All below the ~16K non-streaming
# SDK guidance.
MAX_TOKENS = {HAIKU: 4096, SONNET: 8192, FABLE: 12288}

# ─── Classification → routing policy ─────────────────────────────────────────
#
# complexity:    low | medium | high      (how much reasoning is required)
# reversibility: safe | guarded | irreversible
#   safe         — trivially undone (docs, formatting, local code w/ review)
#   guarded      — costly to redo but recoverable (schema change behind a
#                  migration, deploy with rollback)
#   irreversible — frozen after deployment / touches live trading logic /
#                  external side effects that cannot be unwound

BASE_TIER = {"low": HAIKU, "medium": SONNET, "high": FABLE}


def route(complexity: str, reversibility: str) -> tuple[str, str]:
    """Return (model, justification)."""
    model = BASE_TIER.get(complexity, SONNET)
    why = f"complexity={complexity}"
    if reversibility == "irreversible":
        model = FABLE
        why += "; irreversible → forced to top tier"
    elif reversibility == "guarded" and model != FABLE:
        model = TIER_ORDER[TIER_ORDER.index(model) + 1]
        why += "; guarded reversibility → bumped one tier"
    else:
        why += f"; reversibility={reversibility}"
    return model, why


# ─── Decomposition (runs on Haiku — decomposition itself must be cheap) ──────

DECOMPOSE_SYSTEM = """You are the task decomposer for MarketFlow AI \
(React dashboard, Go backend, Python LangGraph trading brain, Alpaca paper \
trading, Oracle Cloud deploy, human Green Light gate for all trades).

Break the user's task into 2-8 discrete, ordered subtasks (requirements → \
design → implementation → testing → deployment → documentation, as \
applicable — omit stages that don't apply). For each subtask assign:

- "complexity": "low" | "medium" | "high" — how much reasoning it truly \
requires. Product/architecture decisions and trading-logic changes are \
"high". Standard implementation/refactoring/tests are "medium". Deployments, \
config edits, formatting, boilerplate, summaries are "low".
- "reversibility": "safe" | "guarded" | "irreversible" — "irreversible" for \
anything frozen after deployment, touching live trading logic, or with \
external side effects that can't be unwound; "guarded" for recoverable-but-\
costly (migrations, deploys with rollback); "safe" otherwise.

Respond with ONLY a JSON array, no prose:
[{"id": 1, "title": "...", "description": "...", "complexity": "...", "reversibility": "..."}]"""


@dataclass
class Subtask:
    id: int
    title: str
    description: str
    complexity: str
    reversibility: str
    model: str = ""
    justification: str = ""
    est_input_tokens: int = 0
    est_cost_usd: float = 0.0
    result: str | None = None
    status: str = "planned"  # planned | done | escalated_done | human_review
    attempts: list[dict] = field(default_factory=list)


def extract_json_array(text: str) -> list[dict]:
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end <= start:
        raise ValueError(f"no JSON array in model output: {text[:200]!r}")
    return json.loads(text[start : end + 1])


def decompose(client: anthropic.Anthropic, task: str, max_subtasks: int) -> list[Subtask]:
    resp = client.messages.create(
        model=HAIKU,
        max_tokens=2048,
        system=DECOMPOSE_SYSTEM,
        messages=[{"role": "user", "content": task}],
    )
    text = next(b.text for b in resp.content if b.type == "text")
    items = extract_json_array(text)[:max_subtasks]
    subtasks = []
    for i, it in enumerate(items, 1):
        subtasks.append(
            Subtask(
                id=int(it.get("id", i)),
                title=str(it.get("title", f"subtask {i}")),
                description=str(it.get("description", "")),
                complexity=str(it.get("complexity", "medium")).lower(),
                reversibility=str(it.get("reversibility", "safe")).lower(),
            )
        )
    return subtasks


# ─── Dispatch — per-model API constraints matter here ────────────────────────

EXEC_SYSTEM = """You are executing one subtask of a larger MarketFlow AI \
workflow (React + Go + Python LangGraph trading system, Alpaca paper \
trading, Green Light human-approval gate). Do the subtask well and \
concisely. End your reply with a final line exactly of the form:
CONFIDENCE: <0.00-1.00>
scoring how confident you are that your output is correct and complete."""

CONF_RE = re.compile(r"CONFIDENCE:\s*([01](?:\.\d+)?)")


def dispatch_once(client: anthropic.Anthropic, model: str, subtask: Subtask, task: str) -> dict:
    """One API call on `model`. Returns attempt record with usage + result."""
    user = f"Overall task: {task}\n\nYour subtask (#{subtask.id} {subtask.title}):\n{subtask.description}"
    t0 = time.time()

    if model == FABLE:
        # Fable 5: thinking is always on — never send a `thinking` param
        # (explicit disabled/enabled both 400). No temperature/top_p/top_k.
        # Opt into server-side refusal fallbacks by default per API guidance.
        resp = client.beta.messages.create(
            model=FABLE,
            max_tokens=MAX_TOKENS[FABLE],
            betas=["server-side-fallback-2026-06-01"],
            fallbacks=[{"model": "claude-opus-4-8"}],
            system=EXEC_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )
    else:
        # Sonnet 5: adaptive thinking is the default when `thinking` is
        # omitted; manual thinking config and non-default sampling params
        # (temperature/top_p/top_k) return 400 — send neither.
        # Haiku 4.5: plain call; `effort` is unsupported — don't send it.
        resp = client.messages.create(
            model=model,
            max_tokens=MAX_TOKENS[model],
            system=EXEC_SYSTEM,
            messages=[{"role": "user", "content": user}],
        )

    duration = time.time() - t0
    refused = resp.stop_reason == "refusal"
    text = "" if refused else "".join(b.text for b in resp.content if b.type == "text")
    m = CONF_RE.search(text)
    confidence = float(m.group(1)) if m else None

    pin, pout = price_per_mtok(model)
    cost = resp.usage.input_tokens / 1e6 * pin + resp.usage.output_tokens / 1e6 * pout

    return {
        "model": resp.model,
        "requested_model": model,
        "refused": refused,
        "truncated": resp.stop_reason == "max_tokens",
        "confidence": confidence,
        "text": text,
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
        "cost_usd": round(cost, 6),
        "duration_s": round(duration, 2),
    }


def attempt_ok(a: dict) -> bool:
    if a["refused"] or a["truncated"]:
        return False
    if len(a["text"].strip()) < 40:
        return False
    if a["confidence"] is not None and a["confidence"] < 0.60:
        return False
    return True


def execute(client: anthropic.Anthropic, subtask: Subtask, task: str) -> None:
    """Dispatch with the escalation rule: one tier up, once, then human review."""
    a = dispatch_once(client, subtask.model, subtask, task)
    subtask.attempts.append(a)
    if attempt_ok(a):
        subtask.result, subtask.status = a["text"], "done"
        return

    idx = TIER_ORDER.index(subtask.model)
    if idx + 1 < len(TIER_ORDER):
        higher = TIER_ORDER[idx + 1]
        a2 = dispatch_once(client, higher, subtask, task)
        a2["escalated_from"] = subtask.model
        subtask.attempts.append(a2)
        if attempt_ok(a2):
            subtask.result, subtask.status = a2["text"], "escalated_done"
            return

    # Already at top tier, or the escalation also failed → human review
    # (consistent with the Green Light Gate: uncertain output never
    # auto-passes downstream).
    subtask.status = "human_review"


# ─── Cost estimation (count_tokens per assigned model — token counts are
#     model-specific; Sonnet 5's tokenizer yields ~30% more than 4.6) ─────────

ASSUMED_OUTPUT_TOKENS = 1200


def estimate(client: anthropic.Anthropic, subtask: Subtask, task: str) -> None:
    user = f"Overall task: {task}\n\nYour subtask (#{subtask.id} {subtask.title}):\n{subtask.description}"
    n = client.messages.count_tokens(
        model=subtask.model,
        system=EXEC_SYSTEM,
        messages=[{"role": "user", "content": user}],
    ).input_tokens
    pin, pout = price_per_mtok(subtask.model)
    subtask.est_input_tokens = n
    subtask.est_cost_usd = round(n / 1e6 * pin + ASSUMED_OUTPUT_TOKENS / 1e6 * pout, 6)


# ─── Logging (SQLite today; identical DDL works on Supabase/Postgres) ────────

DDL = """CREATE TABLE IF NOT EXISTS model_routing_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    ts            TEXT NOT NULL,
    task          TEXT NOT NULL,
    subtask       TEXT NOT NULL,
    complexity    TEXT NOT NULL,
    reversibility TEXT NOT NULL,
    model         TEXT NOT NULL,
    escalated_from TEXT,
    status        TEXT NOT NULL,
    confidence    REAL,
    input_tokens  INTEGER NOT NULL DEFAULT 0,
    output_tokens INTEGER NOT NULL DEFAULT 0,
    cost_usd      REAL NOT NULL DEFAULT 0,
    duration_s    REAL NOT NULL DEFAULT 0
);"""


def log_attempts(db_path: str, task: str, subtasks: list[Subtask]) -> int:
    conn = sqlite3.connect(db_path)
    conn.execute(DDL)
    n = 0
    for s in subtasks:
        for a in s.attempts:
            conn.execute(
                """INSERT INTO model_routing_log
                   (ts, task, subtask, complexity, reversibility, model,
                    escalated_from, status, confidence, input_tokens,
                    output_tokens, cost_usd, duration_s)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    dt.datetime.now(dt.timezone.utc).isoformat(),
                    task, s.title, s.complexity, s.reversibility,
                    a["requested_model"], a.get("escalated_from"), s.status,
                    a["confidence"], a["input_tokens"], a["output_tokens"],
                    a["cost_usd"], a["duration_s"],
                ),
            )
            n += 1
    conn.commit()
    conn.close()
    return n


# ─── Output ──────────────────────────────────────────────────────────────────

def print_routing_table(task: str, subtasks: list[Subtask]) -> None:
    print(f"\n## Routing table — {task}\n")
    print("| # | Subtask | Model | Justification | Est. tokens in | Cost tier | Est. $ |")
    print("|---|---------|-------|---------------|----------------|-----------|--------|")
    total = 0.0
    for s in subtasks:
        total += s.est_cost_usd
        print(
            f"| {s.id} | {s.title} | `{s.model}` | {s.justification} "
            f"| {s.est_input_tokens} | {COST_TIER[s.model]} | ${s.est_cost_usd:.4f} |"
        )
    pin_f, pout_f = price_per_mtok(FABLE)
    all_fable = sum(
        s.est_input_tokens / 1e6 * pin_f + ASSUMED_OUTPUT_TOKENS / 1e6 * pout_f
        for s in subtasks
    )
    print(f"\nEstimated cost (routed): ${total:.4f}")
    print(f"Estimated cost (all-Fable baseline): ${all_fable:.4f}")
    if all_fable > 0:
        print(f"Estimated savings: {100 * (1 - total / all_fable):.0f}%")


def main() -> int:
    ap = argparse.ArgumentParser(description="MarketFlow AI model router")
    ap.add_argument("task", help="the high-level task to decompose and route")
    ap.add_argument("--execute", action="store_true", help="dispatch each subtask (costs money)")
    ap.add_argument("--db", default="infra/db/marketflow.db", help="SQLite DB for the routing log")
    ap.add_argument("--max-subtasks", type=int, default=8)
    args = ap.parse_args()

    client = anthropic.Anthropic()  # resolves key/profile from environment

    print(f"Decomposing on {HAIKU} …", file=sys.stderr)
    subtasks = decompose(client, args.task, args.max_subtasks)

    for s in subtasks:
        s.model, s.justification = route(s.complexity, s.reversibility)
        estimate(client, s, args.task)

    print_routing_table(args.task, subtasks)

    if not args.execute:
        print("\n(plan only — rerun with --execute to dispatch)")
        return 0

    for s in subtasks:
        print(f"\n→ [{s.id}] {s.title} on {s.model} …", file=sys.stderr)
        execute(client, s, args.task)
        last = s.attempts[-1]
        print(
            f"  {s.status} · conf={last['confidence']} · "
            f"{last['input_tokens']}in/{last['output_tokens']}out · ${last['cost_usd']:.4f}",
            file=sys.stderr,
        )
        if s.status == "human_review":
            print(f"  ⚠ flagged for HUMAN REVIEW (Green Light) — do not auto-consume", file=sys.stderr)

    n = log_attempts(args.db, args.task, subtasks)
    spent = sum(a["cost_usd"] for s in subtasks for a in s.attempts)
    print(f"\nLogged {n} attempt(s) to {args.db} (model_routing_log). Actual spend: ${spent:.4f}")

    for s in subtasks:
        print(f"\n### [{s.id}] {s.title} — {s.status} ({s.model})\n")
        print(s.result or "(no accepted output — human review required)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
