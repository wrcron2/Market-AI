#!/usr/bin/env python3
"""
preflight.py — Pre-market self-test for MarketFlow AI (runs on the Oracle host).

Runs a couple of hours BEFORE market open (cron: 11:00 UTC = 7:00am ET, Mon-Fri)
so any broken dependency is caught and emailed while there is still time to fix
it before the 9:30 ET open.

Checks (all live, no mocks):
  1. Go backend API up            (/api/stats)
  2. Frontend up                  (:3000)
  3. Brain heartbeat fresh        (logs/brain_heartbeat.json < 15 min old)
  4. Ollama up + models present   (qwen3:4b, deepseek-r1:7b)
  5. LLM format sanity            (plain-text HOLD test per model — this exact
                                   check would have caught the 2026-07 forced-JSON
                                   bug that silenced the position monitor)
  6. Alpaca auth + account OK     (/v2/account not blocked, /v2/clock)
  7. Yahoo Finance reachable      (chart API for XLE)
  8. Disk space                   (> 2 GB free)
  9. RESEND_API_KEY present       (warn only)

Result: posts to the dashboard (/api/alerts) AND emails via Resend —
HIGH on any failure, MEDIUM "Preflight PASSED" daily confidence email otherwise.

Stdlib only — no pip dependencies on the host.
Usage: python3 preflight.py [--repo /home/ubuntu/Market-AI]
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
import urllib.request
import urllib.error

BACKEND = "http://127.0.0.1:8080"
FRONTEND = "http://127.0.0.1:3000"
OLLAMA = "http://127.0.0.1:11434"
REQUIRED_MODELS = ["qwen3:4b", "deepseek-r1:7b"]
HEARTBEAT_MAX_AGE = 15 * 60  # seconds
RESEND_API_URL = "https://api.resend.com/emails"


def _http(url: str, data: dict | None = None, headers: dict | None = None,
          timeout: int = 15) -> tuple[int, str]:
    req = urllib.request.Request(
        url,
        data=json.dumps(data).encode() if data is not None else None,
        headers={"Content-Type": "application/json", **(headers or {})},
        method="POST" if data is not None else "GET",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, resp.read().decode(errors="replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode(errors="replace")
    except Exception as e:
        return 0, str(e)


def _load_env(repo: str) -> dict[str, str]:
    env: dict[str, str] = {}
    try:
        with open(os.path.join(repo, ".env")) as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    env[k.strip()] = v.strip()
    except OSError:
        pass
    return env


# ── Checks ────────────────────────────────────────────────────────────────────

def check_backend() -> tuple[bool, str]:
    status, body = _http(f"{BACKEND}/api/stats", timeout=10)
    return status == 200, f"HTTP {status}"


def check_frontend() -> tuple[bool, str]:
    status, _ = _http(FRONTEND, timeout=10)
    return status == 200, f"HTTP {status}"


def check_heartbeat(repo: str) -> tuple[bool, str]:
    path = os.path.join(repo, "logs", "brain_heartbeat.json")
    try:
        age = time.time() - os.path.getmtime(path)
    except OSError:
        return False, "heartbeat file missing (brain not writing /app/logs?)"
    if age > HEARTBEAT_MAX_AGE:
        return False, f"heartbeat stale: {int(age)}s old"
    try:
        with open(path) as f:
            hb = json.load(f)
        return True, f"{int(age)}s old, window={hb.get('window')}, bar={hb.get('bar')}"
    except (OSError, ValueError):
        return True, f"{int(age)}s old (unparseable body)"


def check_ollama_models() -> tuple[bool, str]:
    status, body = _http(f"{OLLAMA}/api/tags", timeout=10)
    if status != 200:
        return False, f"Ollama unreachable: HTTP {status}"
    try:
        names = [m["name"] for m in json.loads(body).get("models", [])]
    except ValueError:
        return False, "bad /api/tags response"
    missing = [m for m in REQUIRED_MODELS if m not in names]
    return (not missing), ("all models present" if not missing else f"missing: {missing}")


def _ollama_chat(model: str, system: str, user: str, fmt,
                 num_predict: int) -> tuple[float, str | None, str]:
    """One live Ollama call. fmt: None | "json" | JSON-schema dict."""
    payload = {
        "model": model,
        "messages": [{"role": "system", "content": system},
                     {"role": "user", "content": user}],
        "stream": False,
        "think": False,
        "options": {"num_predict": num_predict, "num_ctx": 1024},
    }
    if fmt is not None:
        payload["format"] = fmt
    t0 = time.time()
    status, body = _http(f"{OLLAMA}/api/chat", data=payload, timeout=300)
    dt = time.time() - t0
    if status != 200:
        return dt, None, f"HTTP {status}: {body[:100]}"
    try:
        return dt, json.loads(body)["message"]["content"].strip(), ""
    except (ValueError, KeyError):
        return dt, None, f"bad response body: {body[:100]}"


def check_llm_format(model: str) -> tuple[bool, str]:
    """
    Live plain-text generation test — mirrors the position monitor's real call
    (deepseek-r1:7b in production). A model that cannot answer "HOLD. <reason>"
    here silently disables the monitor, so fail loudly now.
    """
    system = ('You are a position manager. Respond with EXACTLY one of:\n'
              'HOLD. [reason]\nSELL. [reason]\nUNCERTAIN. [reason]')
    user = ("Symbol: TEST\nSide: LONG\nEntry: 100.00\nCurrent: 102.00\n"
            "Unrealized P/L: +2.00 percent\n\nShould we HOLD, SELL, or are you UNCERTAIN?")
    dt, content, err = _ollama_chat(model, system, user, fmt=None, num_predict=40)
    if content is None:
        return False, err
    first = content.split()[0].rstrip(".,:").upper() if content else ""
    ok = first in ("HOLD", "SELL", "UNCERTAIN")
    return ok, f"{'ok' if ok else 'BAD FORMAT'} ({dt:.0f}s): {content[:50]!r}"


def check_llm_json(model: str) -> tuple[bool, str]:
    """
    Live JSON generation test — mirrors the signal pipeline's real call:
    production passes a pydantic JSON schema as the Ollama `format`, which
    constrains the output structure. Bare format="json" is NOT what production
    does (and qwen3:4b echoes the prompt under it).
    """
    schema = {
        "type": "object",
        "properties": {
            "decision": {"type": "string", "enum": ["BUY", "SKIP"]},
            "confidence": {"type": "number"},
        },
        "required": ["decision", "confidence"],
    }
    system = 'Respond ONLY with JSON: {"decision": "BUY"|"SKIP", "confidence": 0.0-1.0}'
    user = "RSI 28, price at lower Bollinger band, VIX 15. BUY or SKIP?"
    dt, content, err = _ollama_chat(model, system, user, fmt=schema, num_predict=60)
    if content is None:
        return False, err
    try:
        obj = json.loads(content)
        ok = obj.get("decision") in ("BUY", "SKIP")
    except ValueError:
        ok = False
    return ok, f"{'ok' if ok else 'BAD JSON'} ({dt:.0f}s): {content[:50]!r}"


def check_alpaca(env: dict[str, str]) -> tuple[bool, str]:
    key, secret = env.get("ALPACA_API_KEY", ""), env.get("ALPACA_SECRET_KEY", "")
    if not key or not secret:
        return False, "ALPACA keys missing from .env"
    base = env.get("ALPACA_BASE_URL", "https://paper-api.alpaca.markets").rstrip("/")
    hdrs = {"APCA-API-KEY-ID": key, "APCA-API-SECRET-KEY": secret}
    status, body = _http(f"{base}/v2/account", headers=hdrs, timeout=15)
    if status != 200:
        return False, f"/v2/account HTTP {status}"
    try:
        acct = json.loads(body)
    except ValueError:
        return False, "bad account body"
    if acct.get("trading_blocked") or acct.get("account_blocked"):
        return False, "account BLOCKED"
    status2, _ = _http(f"{base}/v2/clock", headers=hdrs, timeout=15)
    return status2 == 200, (f"status={acct.get('status')}, "
                            f"equity=${acct.get('equity')}, clock HTTP {status2}")


def check_yahoo() -> tuple[bool, str]:
    status, body = _http(
        "https://query1.finance.yahoo.com/v8/finance/chart/XLE?range=1d&interval=1d",
        headers={"User-Agent": "Mozilla/5.0"}, timeout=15)
    if status != 200:
        return False, f"HTTP {status}"
    ok = '"close"' in body
    return ok, "chart data OK" if ok else "no close data in response"


def check_disk() -> tuple[bool, str]:
    free_gb = shutil.disk_usage("/").free / 1e9
    return free_gb > 2.0, f"{free_gb:.1f} GB free"


def check_resend(env: dict[str, str]) -> tuple[bool, str]:
    return bool(env.get("RESEND_API_KEY")), (
        "key present" if env.get("RESEND_API_KEY") else "RESEND_API_KEY missing — email alerts DISABLED")


# ── Reporting ─────────────────────────────────────────────────────────────────

def post_dashboard(severity: str, title: str, body: str) -> None:
    _http(f"{BACKEND}/api/alerts",
          data={"severity": severity, "title": title, "body": body}, timeout=5)


def send_email(env: dict[str, str], severity: str, title: str, body: str) -> None:
    key = env.get("RESEND_API_KEY")
    if not key:
        return
    emoji = {"HIGH": "🟠", "MEDIUM": "🟡"}.get(severity, "⚪")
    _http(RESEND_API_URL, data={
        "from": "MarketFlow AI <onboarding@resend.dev>",
        "to": [env.get("ALERT_EMAIL_TO", "wrcron1@gmail.com")],
        "subject": f"{emoji} [{severity}] MarketFlow AI — {title}",
        "html": "<pre style='font-family:monospace'>" + body + "</pre>",
    }, headers={"Authorization": f"Bearer {key}"}, timeout=15)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", default="/home/ubuntu/Market-AI")
    args = ap.parse_args()
    env = _load_env(args.repo)

    checks: list[tuple[str, bool, str, bool]] = []  # name, ok, detail, hard

    def run(name: str, fn, *fargs, hard: bool = True):
        try:
            ok, detail = fn(*fargs)
        except Exception as exc:  # a crashing check is a failing check
            ok, detail = False, f"check crashed: {exc}"
        checks.append((name, ok, detail, hard))
        print(f"{'PASS' if ok else 'FAIL'}  {name:<28} {detail}")

    run("backend_api", check_backend)
    run("frontend", check_frontend)
    run("brain_heartbeat", check_heartbeat, args.repo)
    run("ollama_models", check_ollama_models)
    # Test each model in its PRODUCTION role: qwen3:4b answers JSON for the
    # signal pipeline; deepseek-r1:7b answers plain text for the monitor.
    run("llm_json[qwen3:4b]", check_llm_json, "qwen3:4b")
    run("llm_format[deepseek-r1:7b]", check_llm_format, "deepseek-r1:7b")
    run("alpaca_account", check_alpaca, env)
    run("yahoo_feed", check_yahoo)
    run("disk_space", check_disk)
    run("resend_key", check_resend, env, hard=False)

    hard_failures = [c for c in checks if not c[1] and c[3]]
    lines = [f"{'✅' if ok else ('⚠️' if not hard else '❌')} {name}: {detail}"
             for name, ok, detail, hard in checks]
    body = "\n".join(lines)

    if hard_failures:
        title = f"Preflight FAILED ({len(hard_failures)}/{len(checks)} checks)"
        post_dashboard("HIGH", title, body)
        send_email(env, "HIGH", title, body)
        print(f"\n{title}")
        return 1
    title = f"Preflight PASSED ({len(checks)} checks) — system ready for market open"
    post_dashboard("MEDIUM", title, body)
    send_email(env, "MEDIUM", title, body)
    print(f"\n{title}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
