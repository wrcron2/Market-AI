#!/usr/bin/env python3
"""
model_eval.py — Trading-reasoning model evaluation for MarketFlow AI.

Answers the question: "are the models we run good enough to handle the trading
reasoning?" with measurements instead of vibes. Runs the SAME prompts the
production system uses (position-monitor plain-text decisions + signal-style
JSON) across every available model and scores:

  format %      — did the answer follow the demanded format? (the 2026-07 bug
                  class: a model that can't answer "HOLD. <reason>" silently
                  disables the position monitor)
  correct %     — on scenarios with an objectively right answer (e.g. -12% loss
                  with a stated 5% stop rule MUST be SELL)
  consistent    — same prompt twice → same decision?
  latency       — median seconds per call

Models:
  ollama/qwen3:4b, ollama/deepseek-r1:7b      (local, always)
  groq/llama-3.3-70b-versatile                (if GROQ_API_KEY set)
  anthropic/claude-opus-4-8                   (if ANTHROPIC_API_KEY set and the
                                               `anthropic` SDK is installed)

Run on the Oracle host:  python3 scripts/model_eval.py --repo /home/ubuntu/Market-AI
Cloud cost: ~10 tiny calls per cloud model (< $0.10).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
import sys
import time
import urllib.request
import urllib.error

OLLAMA = os.getenv("OLLAMA_EVAL_HOST", "http://127.0.0.1:11434")

MONITOR_SYSTEM = (
    "You are a position manager for a paper trading account.\n"
    "Risk rules: SELL when the unrealized loss exceeds 5%. Prefer HOLD when the\n"
    "position is quietly profitable with no exit signal. Say UNCERTAIN only when\n"
    "you genuinely cannot decide.\n"
    "Respond with EXACTLY ONE of these three options (nothing else):\n"
    "HOLD. [One sentence reason.]\n"
    "SELL. [One sentence reason.]\n"
    "UNCERTAIN. [One sentence reason.]"
)

SIGNAL_SYSTEM = (
    "You are a trading signal agent. Respond ONLY with a JSON object:\n"
    '{"decision": "BUY"|"SKIP", "confidence": <0.0-1.0>, "reason": "<one sentence>"}'
)


def _pos_prompt(sym, entry, cur, plpc, extra=""):
    return (f"Symbol: {sym}\nSide: LONG\nQuantity: 100 shares\n"
            f"Entry price: {entry:.2f}\nCurrent price: {cur:.2f}\n"
            f"Unrealized P/L: {plpc:+.2f} percent\n{extra}\n"
            "Should we HOLD, SELL, or are you UNCERTAIN?")


# (name, system, user, kind, expected-or-None)
SCENARIOS = [
    ("deep_loss_must_sell", MONITOR_SYSTEM,
     _pos_prompt("XYZ", 100.0, 88.0, -12.0), "monitor", "SELL"),
    ("quiet_profit_hold", MONITOR_SYSTEM,
     _pos_prompt("ABC", 50.0, 51.1, 2.2, "No exit signal; momentum stable."),
     "monitor", "HOLD"),
    ("small_loss_within_stop", MONITOR_SYSTEM,
     _pos_prompt("DEF", 200.0, 196.0, -2.0, "Above SMA20; trend intact."),
     "monitor", "HOLD"),
    ("breach_stop_barely", MONITOR_SYSTEM,
     _pos_prompt("GHI", 80.0, 74.8, -6.5), "monitor", "SELL"),
    ("ambiguous_ok_any", MONITOR_SYSTEM,
     _pos_prompt("JKL", 120.0, 121.0, 0.8, "Mixed signals: RSI overbought but MACD rising."),
     "monitor", None),
    ("json_signal_buy", SIGNAL_SYSTEM,
     "RSI 28 (oversold), price at lower Bollinger band, volume contracting, VIX 15. "
     "Mean-reversion setup. BUY or SKIP?", "json", None),
    ("json_signal_skip", SIGNAL_SYSTEM,
     "RSI 55, price mid-range, no indicator at an extreme, volume average. BUY or SKIP?",
     "json", None),
]

_DECISIONS = ("HOLD", "SELL", "UNCERTAIN")
_THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)


def parse_monitor(raw: str) -> str | None:
    text = _THINK_RE.sub("", raw or "").strip()
    if not text:
        return None
    first = text.split()[0].strip(".,:;!\"'").upper()
    return first if first in _DECISIONS else None


def parse_json(raw: str) -> bool:
    text = _THINK_RE.sub("", raw or "").strip()
    m = re.search(r"\{.*\}", text, re.DOTALL)
    if not m:
        return False
    try:
        obj = json.loads(m.group(0))
        return obj.get("decision") in ("BUY", "SKIP") and isinstance(obj.get("confidence"), (int, float))
    except (ValueError, TypeError):
        return False


# ── Backends ──────────────────────────────────────────────────────────────────

def call_ollama(model: str, system: str, user: str, json_mode: bool) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "stream": False, "think": False,
        "options": {"num_predict": 120, "num_ctx": 2048},
    }
    if json_mode:
        payload["format"] = "json"
    req = urllib.request.Request(f"{OLLAMA}/api/chat",
                                 data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=600) as resp:
        return json.loads(resp.read())["message"]["content"]


def call_groq(model: str, system: str, user: str, json_mode: bool) -> str:
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "max_tokens": 200, "temperature": 0,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}
    req = urllib.request.Request(
        "https://api.groq.com/openai/v1/chat/completions",
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json",
                 "Authorization": f"Bearer {os.environ['GROQ_API_KEY']}"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.loads(resp.read())["choices"][0]["message"]["content"]


def call_anthropic(model: str, system: str, user: str, json_mode: bool) -> str:
    # Official Anthropic SDK — see claude-api skill; do not replace with raw HTTP.
    import anthropic
    client = anthropic.Anthropic()
    response = client.messages.create(
        model=model,
        max_tokens=300,
        system=system,
        messages=[{"role": "user", "content": user}],
    )
    return next((b.text for b in response.content if b.type == "text"), "")


# ── Runner ────────────────────────────────────────────────────────────────────

def evaluate(label: str, call, model: str) -> dict:
    fmt_ok = fmt_total = correct = correct_total = 0
    consistent = consist_total = 0
    latencies: list[float] = []
    details = []
    for name, system, user, kind, expected in SCENARIOS:
        json_mode = kind == "json"
        answers = []
        for _ in range(2):  # consistency = 2 runs per scenario
            t0 = time.time()
            try:
                raw = call(model, system, user, json_mode)
            except Exception as exc:
                details.append(f"  {name}: CALL FAILED — {exc}")
                raw = ""
            latencies.append(time.time() - t0)
            answers.append(raw)

        fmt_total += 2
        if kind == "monitor":
            parsed = [parse_monitor(a) for a in answers]
            fmt_ok += sum(1 for p in parsed if p is not None)
            if expected:
                correct_total += 2
                correct += sum(1 for p in parsed if p == expected)
            consist_total += 1
            if parsed[0] is not None and parsed[0] == parsed[1]:
                consistent += 1
            details.append(f"  {name}: {parsed} (expected {expected or 'any'})")
        else:
            oks = [parse_json(a) for a in answers]
            fmt_ok += sum(oks)
            consist_total += 1
            if all(oks):
                consistent += 1
            details.append(f"  {name}: json_valid={oks}")

    return {
        "model": label,
        "format_pct": 100.0 * fmt_ok / max(fmt_total, 1),
        "correct_pct": (100.0 * correct / correct_total) if correct_total else None,
        "consistency_pct": 100.0 * consistent / max(consist_total, 1),
        "median_latency_s": statistics.median(latencies) if latencies else None,
        "details": details,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default=os.getcwd())
    ap.add_argument("--skip-cloud", action="store_true")
    args = ap.parse_args()

    # load .env for keys if present
    try:
        with open(os.path.join(args.repo, ".env")) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    os.environ.setdefault(k.strip(), v.strip())
    except OSError:
        pass

    runs = [("ollama/qwen3:4b", call_ollama, "qwen3:4b"),
            ("ollama/deepseek-r1:7b", call_ollama, "deepseek-r1:7b")]
    if not args.skip_cloud:
        if os.environ.get("GROQ_API_KEY"):
            runs.append(("groq/llama-3.3-70b-versatile", call_groq, "llama-3.3-70b-versatile"))
        if os.environ.get("ANTHROPIC_API_KEY"):
            try:
                import anthropic  # noqa: F401
                runs.append(("anthropic/claude-opus-4-8", call_anthropic, "claude-opus-4-8"))
            except ImportError:
                print("NOTE: ANTHROPIC_API_KEY set but `anthropic` SDK not installed "
                      "(pip3 install anthropic) — skipping Claude leg.")

    results = []
    for label, call, model in runs:
        print(f"\n=== {label} ===")
        r = evaluate(label, call, model)
        for d in r["details"]:
            print(d)
        results.append(r)

    print("\n" + "=" * 78)
    print(f"{'model':<32}{'format%':>9}{'correct%':>10}{'consist%':>10}{'med s':>8}")
    print("-" * 78)
    for r in results:
        c = f"{r['correct_pct']:.0f}" if r["correct_pct"] is not None else "—"
        print(f"{r['model']:<32}{r['format_pct']:>8.0f}%{c:>9}%{r['consistency_pct']:>9.0f}%"
              f"{r['median_latency_s']:>8.1f}")
    print("=" * 78)

    out = os.path.join(args.repo, "reports", f"model_eval_{time.strftime('%Y%m%d_%H%M')}.json")
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with open(out, "w") as f:
            json.dump(results, f, indent=2)
        print(f"saved: {out}")
    except OSError as exc:
        print(f"could not save report: {exc}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
