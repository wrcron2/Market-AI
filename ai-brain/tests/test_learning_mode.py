"""
test_learning_mode.py — Block 1: learning-only enforcement (Python brain side)
==============================================================================
Offline only: no network and no real broker. Third-party modules that
main.py / alpaca_executor.py import at load time (httpx, structlog,
python-dotenv, agents.telemetry) are stubbed in sys.modules ONLY when the real
module cannot be imported — in a fully provisioned checkout the real modules
are used and nothing here touches the network anyway: broker HTTP is captured
by swapping the executor's clients for fake recorders, and the learning branch
is verified with spies on main.py's own collaborators.

Runs under pytest (python -m pytest ai-brain/tests/test_learning_mode.py) and
standalone (python3 ai-brain/tests/test_learning_mode.py) for environments
where pytest is not installed.
"""
from __future__ import annotations

import contextlib
import importlib
import json
import os
import sys
import tempfile
import types
from pathlib import Path

AI_BRAIN_DIR = Path(__file__).resolve().parents[1]
if str(AI_BRAIN_DIR) not in sys.path:
    sys.path.insert(0, str(AI_BRAIN_DIR))

from execution import operating_mode  # pure stdlib — no stubs needed


# ── Conditional sys.modules stubs (only when the real import fails) ──────────

class _StubLogger:
    def __init__(self):
        self.events = []

    def _record(self, level, event, **kw):
        self.events.append((level, event, kw))

    def info(self, event, **kw):
        self._record("info", event, **kw)

    def warning(self, event, **kw):
        self._record("warning", event, **kw)

    def error(self, event, **kw):
        self._record("error", event, **kw)

    def debug(self, event, **kw):
        self._record("debug", event, **kw)


STUB_LOGGER = _StubLogger()


class _FakeResponse:
    def __init__(self, status_code=200, payload=None):
        self.status_code = status_code
        self._payload = {} if payload is None else payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"fake http status {self.status_code}")


class _FakeHttpxClient:
    """Records every request; serves canned broker replies. Never does I/O."""

    def __init__(self, base_url="", headers=None, timeout=None):
        self.base_url = base_url
        self.headers = headers or {}
        self.timeout = timeout
        self.requests = []  # list of (method, path)

    def get(self, path, **kw):
        self.requests.append(("GET", path))
        if path == "/v2/account":
            return _FakeResponse(200, {"cash": "10000.00", "status": "ACTIVE"})
        if path == "/v2/positions":
            return _FakeResponse(200, [])
        if path.startswith("/v2/positions/"):
            return _FakeResponse(200, {"symbol": "AAPL", "current_price": "123.45"})
        return _FakeResponse(200, {})

    def post(self, path, **kw):
        self.requests.append(("POST", path))
        return _FakeResponse(200, {"id": "order-1", "status": "accepted"})

    def delete(self, path, **kw):
        self.requests.append(("DELETE", path))
        return _FakeResponse(200, {"id": "close-1"})


def _ensure_stubbed_modules() -> None:
    """Install a stub only for modules that cannot be imported for real."""
    try:
        importlib.import_module("structlog")
    except ImportError:
        structlog = types.ModuleType("structlog")
        structlog.configure = lambda **kw: None
        structlog.make_filtering_bound_logger = lambda level: None
        structlog.get_logger = lambda *a, **kw: STUB_LOGGER
        sys.modules["structlog"] = structlog

    try:
        importlib.import_module("httpx")
    except ImportError:
        httpx = types.ModuleType("httpx")
        httpx.Client = _FakeHttpxClient
        httpx.get = lambda url, **kw: _FakeResponse(200, {"mode": "yahoo"})
        sys.modules["httpx"] = httpx

    try:
        importlib.import_module("dotenv")
    except ImportError:
        dotenv = types.ModuleType("dotenv")
        dotenv.load_dotenv = lambda *a, **kw: None
        sys.modules["dotenv"] = dotenv

    try:
        importlib.import_module("agents.telemetry")
    except ImportError:
        agents = types.ModuleType("agents")
        agents.__path__ = []  # package marker so submodule imports resolve
        telemetry = types.ModuleType("agents.telemetry")
        telemetry.emit_activity = lambda *a, **kw: None
        agents.telemetry = telemetry
        sys.modules["agents"] = agents
        sys.modules["agents.telemetry"] = telemetry


_ensure_stubbed_modules()


@contextlib.contextmanager
def patched_env(**overrides):
    """Set env vars (None deletes), restoring the previous values afterwards."""
    sentinel = object()
    old = {k: os.environ.get(k, sentinel) for k in overrides}
    for k, v in overrides.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v
    try:
        yield
    finally:
        for k, v in old.items():
            if v is sentinel:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v


def _import_main():
    return importlib.import_module("main")


def _make_executor(**env):
    """
    Build an AlpacaExecutor under a fully valid explicit paper env, then swap
    both HTTP clients for request recorders so tests observe every call the
    broker boundary lets through (construction itself performs no network I/O).
    """
    base = {
        "DECISION_AUTHORITY": "LEGACY",
        "MARKET_AI_OPERATING_MODE": "paper",
        "PAPER_TRADING": "true",
        "ALPACA_BASE_URL": operating_mode.PAPER_BROKER_BASE_URL,
        "ALPACA_API_KEY": "test-key",
        "ALPACA_SECRET_KEY": "test-secret",
    }
    base.update(env)
    with patched_env(**base):
        from execution.alpaca_executor import AlpacaExecutor
        ex = AlpacaExecutor()
    ex._client = _FakeHttpxClient(base_url=base["ALPACA_BASE_URL"])
    ex._data_client = _FakeHttpxClient(base_url="https://data.alpaca.markets")
    return ex


# ── 1. Mode resolution fails closed ───────────────────────────────────────────

def test_mode_defaults_and_invalid_values_fail_closed():
    for raw in (None, "", "learning", "live", "yahoo", "Paper", "PAPER", "paper ", " paper", "0", "true"):
        assert operating_mode.parse(raw) == operating_mode.LEARNING, f"parse({raw!r}) must fail closed"
    assert operating_mode.parse("paper") == operating_mode.PAPER, "only exact 'paper' may permit paper"

    with patched_env(MARKET_AI_OPERATING_MODE=None):
        assert operating_mode.current_mode() == operating_mode.LEARNING
    with patched_env(MARKET_AI_OPERATING_MODE=""):
        assert operating_mode.current_mode() == operating_mode.LEARNING
    with patched_env(MARKET_AI_OPERATING_MODE="paper"):
        assert operating_mode.current_mode() == operating_mode.PAPER

    assert operating_mode.decisions_enabled(operating_mode.LEARNING) is False
    assert operating_mode.execution_enabled(operating_mode.LEARNING) is False
    assert operating_mode.decisions_enabled(operating_mode.PAPER) is True
    assert operating_mode.execution_enabled(operating_mode.PAPER) is True


def test_mutation_block_reason_matrix():
    # Learning blocks regardless of broker env.
    for pt in (None, "false", "true"):
        with patched_env(MARKET_AI_OPERATING_MODE=None, PAPER_TRADING=pt,
                         ALPACA_BASE_URL=operating_mode.PAPER_BROKER_BASE_URL):
            reason = operating_mode.mutation_block_reason()
            assert reason is not None and reason.startswith("learning_only")

    # Paper without explicit PAPER_TRADING=true blocks.
    for pt in (None, "", "false", "0", "yes"):
        with patched_env(MARKET_AI_OPERATING_MODE="paper", PAPER_TRADING=pt,
                         ALPACA_BASE_URL=operating_mode.PAPER_BROKER_BASE_URL):
            assert operating_mode.mutation_block_reason() is not None, f"PAPER_TRADING={pt!r}"

    # Paper with any non-exact broker URL blocks (live host, insecure scheme,
    # trailing slash, lookalike host, userinfo, path suffix).
    bad_urls = (
        "https://api.alpaca.markets",
        "http://paper-api.alpaca.markets",
        "https://paper-api.alpaca.markets/",
        "https://paper-api.alpaca.markets.evil.example",
        "https://user:secret@paper-api.alpaca.markets",
        "https://paper-api.alpaca.markets/v2",
        "",
    )
    for url in bad_urls:
        with patched_env(MARKET_AI_OPERATING_MODE="paper", PAPER_TRADING="true", ALPACA_BASE_URL=url):
            assert operating_mode.mutation_block_reason() is not None, f"URL {url!r}"

    # The one explicitly valid configuration passes.
    with patched_env(DECISION_AUTHORITY="LEGACY", MARKET_AI_OPERATING_MODE="paper", PAPER_TRADING="true",
                     ALPACA_BASE_URL=operating_mode.PAPER_BROKER_BASE_URL):
        assert operating_mode.mutation_block_reason() is None
    # Unset ALPACA_BASE_URL falls back to the paper default (executor does the same).
    with patched_env(DECISION_AUTHORITY="LEGACY", MARKET_AI_OPERATING_MODE="paper", PAPER_TRADING="true", ALPACA_BASE_URL=None):
        assert operating_mode.mutation_block_reason() is None


# ── 2. Learning branch: heartbeat only, stop condition injected ───────────────

def test_learning_loop_writes_heartbeat_and_stops():
    main = _import_main()
    calls = {"telemetry": 0, "mode_poll": 0, "process": 0, "demo": 0}
    spies = {
        "emit_activity": lambda *a, **kw: calls.__setitem__("telemetry", calls["telemetry"] + 1),
        "_get_current_mode": lambda: calls.__setitem__("mode_poll", calls["mode_poll"] + 1),
        "_process": lambda *a, **kw: calls.__setitem__("process", calls["process"] + 1),
        "_demo_market_data": lambda: calls.__setitem__("demo", calls["demo"] + 1),
    }
    originals = {name: getattr(main, name) for name in spies}

    with tempfile.TemporaryDirectory() as tmp:
        hb_path = os.path.join(tmp, "brain_heartbeat.json")
        old_path = main.HEARTBEAT_PATH
        main.HEARTBEAT_PATH = hb_path
        for name, spy in spies.items():
            setattr(main, name, spy)
        try:
            checks = {"n": 0}

            def stop():
                checks["n"] += 1
                return checks["n"] > 2  # two heartbeats, stop on the third check

            main._run_learning_mode(should_stop=stop, heartbeat_seconds=0.01)

            with open(hb_path) as f:
                hb = json.load(f)
            assert hb["mode"] == "learning", hb
            assert hb["decisions_enabled"] is False, hb
            assert hb["execution_enabled"] is False, hb
            assert hb["bar"] == 2 and isinstance(hb["ts"], int), hb
        finally:
            main.HEARTBEAT_PATH = old_path
            for name, orig in originals.items():
                setattr(main, name, orig)

    assert calls == {"telemetry": 0, "mode_poll": 0, "process": 0, "demo": 0}, (
        f"learning branch invoked business/backend behavior: {calls}")


def test_main_gate_never_imports_decision_or_executor_modules_in_learning():
    main = _import_main()
    for mod in ("agents.orchestrator", "execution.alpaca_executor"):
        sys.modules.pop(mod, None)

    ran = []
    original = main._run_learning_mode
    main._run_learning_mode = lambda **kw: ran.append(kw)
    try:
        with patched_env(MARKET_AI_OPERATING_MODE="bogus-value"):  # fail closed
            main.main()
    finally:
        main._run_learning_mode = original

    assert ran, "learning branch was not taken for an unknown operating mode"
    assert "agents.orchestrator" not in sys.modules, "Orchestrator imported in learning mode"
    assert "execution.alpaca_executor" not in sys.modules, "executor imported in learning mode"


def test_learning_loop_logs_explicit_startup_message():
    main = _import_main()
    seen = []
    orig_info = main.log.info
    main.log.info = lambda event, **kw: seen.append(event)
    with tempfile.TemporaryDirectory() as tmp:
        old_path = main.HEARTBEAT_PATH
        main.HEARTBEAT_PATH = os.path.join(tmp, "hb.json")
        try:
            main._run_learning_mode(should_stop=lambda: True, heartbeat_seconds=0.01)
        finally:
            main.log.info = orig_info
            main.HEARTBEAT_PATH = old_path
    assert "marketflow.brain.learning_mode" in seen, (
        f"explicit startup log explaining disabled behavior missing: {seen}")


# ── 3. Executor broker boundary (policy captured at construction) ────────────

VALID_PAPER_ENV = {
    "DECISION_AUTHORITY": "LEGACY",
    "MARKET_AI_OPERATING_MODE": "paper",
    "PAPER_TRADING": "true",
    "ALPACA_BASE_URL": operating_mode.PAPER_BROKER_BASE_URL,
}


def test_executor_constructed_in_learning_mode_stays_blocked():
    """A client built in learning mode must not become authorized by editing
    the environment to valid paper values afterwards."""
    ex = _make_executor(MARKET_AI_OPERATING_MODE=None)  # constructed in learning mode
    with patched_env(**VALID_PAPER_ENV):  # call-time env is fully valid paper
        for mutation in (
            lambda: ex.place_order("AAPL", "SELL", 1),
            lambda: ex.close_position("AAPL"),
        ):
            try:
                mutation()
                raise AssertionError("mutation must stay blocked for a learning-mode client")
            except operating_mode.LearningModeError as exc:
                assert "learning_only" in str(exc)
    assert ex._client.requests == [], f"outbound calls leaked: {ex._client.requests}"
    assert ex._data_client.requests == [], f"data calls leaked: {ex._data_client.requests}"


def test_executor_constructed_with_invalid_broker_env_stays_blocked():
    """Invalid configuration at construction: the client stays blocked even
    when the environment is corrected to valid paper values at call time."""
    invalid_construction_envs = [
        {"PAPER_TRADING": None},  # explicit value required — unset is captured as blocked
        {"ALPACA_BASE_URL": "https://api.alpaca.markets"},               # live host
        {"ALPACA_BASE_URL": "http://paper-api.alpaca.markets"},          # insecure scheme
        {"ALPACA_BASE_URL": "https://paper-api.alpaca.markets/"},        # suffix
        {"ALPACA_BASE_URL": "https://user:secret@paper-api.alpaca.markets"},  # userinfo
        {"MARKET_AI_OPERATING_MODE": "bogus"},                           # unknown mode
    ]
    for overrides in invalid_construction_envs:
        ex = _make_executor(**overrides)
        with patched_env(**VALID_PAPER_ENV):
            for mutation in (
                lambda: ex.place_order("AAPL", "SELL", 1),
                lambda: ex.close_position("AAPL"),
            ):
                try:
                    mutation()
                    raise AssertionError(f"mutation must stay blocked for construction env {overrides}")
                except operating_mode.LearningModeError:
                    pass
        assert ex._client.requests == [], f"{overrides}: outbound calls leaked: {ex._client.requests}"
        assert ex._data_client.requests == [], f"{overrides}: data calls leaked: {ex._data_client.requests}"


def test_executor_construction_fails_closed_without_paper_trading():
    """PAPER_TRADING=false fails closed at construction (pre-existing gate)."""
    try:
        _make_executor(PAPER_TRADING="false")
        raise AssertionError("construction with PAPER_TRADING=false must fail closed")
    except RuntimeError as exc:
        assert "PAPER_TRADING=true" in str(exc)


def test_executor_live_host_client_never_reached():
    """A client built against a live host must not become authorized by
    pointing ALPACA_BASE_URL at the paper host after construction."""
    ex = _make_executor(ALPACA_BASE_URL="https://api.alpaca.markets")
    assert ex._client.base_url == "https://api.alpaca.markets"
    with patched_env(**VALID_PAPER_ENV):
        try:
            ex.place_order("AAPL", "SELL", 1)
            raise AssertionError("live-host client must stay blocked")
        except operating_mode.LearningModeError:
            pass
        try:
            ex.close_position("AAPL")
            raise AssertionError("live-host client must stay blocked")
        except operating_mode.LearningModeError:
            pass
    assert ex._client.requests == []


def test_executor_reads_remain_usable_in_learning_mode():
    ex = _make_executor(MARKET_AI_OPERATING_MODE=None)  # learning-mode client
    acct = ex.get_account()
    positions = ex.get_all_positions()
    assert acct["status"] == "ACTIVE"
    assert positions == []
    methods = {m for m, _ in ex._client.requests}
    assert methods == {"GET"}, f"learning mode issued non-read calls: {ex._client.requests}"


def test_executor_valid_paper_config_still_allows_mutations():
    """Existing paper behavior under explicit valid config — and the captured
    policy is immutable in both directions: wiping the environment afterwards
    does not change what this client was authorized for at construction."""
    ex = _make_executor()  # constructed under explicit valid paper config
    with patched_env(MARKET_AI_OPERATING_MODE=None, PAPER_TRADING=None, ALPACA_BASE_URL=None):
        order = ex.place_order("AAPL", "SELL", 1)  # SELL: cash guard needs no network
        result = ex.close_position("AAPL")
    assert order["id"] == "order-1"
    assert result["id"] == "close-1"
    assert ex._client.requests == [("POST", "/v2/orders"), ("DELETE", "/v2/positions/AAPL")], ex._client.requests


def test_kimi_authority_never_imports_legacy_brain_even_in_paper():
    import builtins
    main = _import_main()
    original_import, original_loop = builtins.__import__, main._run_learning_mode
    ran = []
    def guarded_import(name, *args, **kwargs):
        if name in ("agents.orchestrator", "agents.position_monitor", "execution.alpaca_executor"):
            raise AssertionError("legacy module imported under KIMI/disabled authority")
        return original_import(name, *args, **kwargs)
    try:
        builtins.__import__ = guarded_import
        main._run_learning_mode = lambda **kw: ran.append(True)
        for authority in ("KIMI", None, "invalid", "legacy"):
            with patched_env(MARKET_AI_OPERATING_MODE="paper", DECISION_AUTHORITY=authority):
                main.main()
        assert len(ran) == 4
    finally:
        builtins.__import__, main._run_learning_mode = original_import, original_loop


def test_kimi_legacy_executor_stays_blocked_after_authority_change():
    ex = _make_executor(DECISION_AUTHORITY="KIMI")
    with patched_env(DECISION_AUTHORITY="LEGACY"):
        for mutation in (lambda: ex.place_order("AAPL", "SELL", 1), lambda: ex.close_position("AAPL")):
            try:
                mutation()
                raise AssertionError("KIMI client used legacy executor")
            except operating_mode.LearningModeError:
                pass
    assert ex._client.requests == []


# ── Standalone runner (pytest is unavailable in this workspace) ───────────────

if __name__ == "__main__":
    failures = 0
    tests = [(name, fn) for name, fn in sorted(globals().items())
             if name.startswith("test_") and callable(fn)]
    for name, fn in tests:
        try:
            fn()
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001 — report every failure, then exit non-zero
            failures += 1
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
    print(f"\n{len(tests) - failures}/{len(tests)} passed")
    sys.exit(1 if failures else 0)
