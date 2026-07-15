"""
telemetry.py — Brain Activity live feed emitter
================================================
Posts one pipeline-step event to the Go backend (POST /api/brain/activity),
which broadcasts it to the dashboard's Brain Activity panel over WebSocket
and keeps it in an in-memory ring buffer for page-refresh backfill.

Shared by the orchestrator (signal/debate/risk/stage/execute steps) and the
main bar loop (scan-level filter decisions), so every symbol drop is visible
on the dashboard — not just the ones that reach the agent pipeline.
"""
from __future__ import annotations

import httpx


def emit_activity(backend_base: str, symbol: str, step: str, status: str, detail: str = "") -> None:
    """
    Fire-and-forget: a telemetry failure must never affect the pipeline.
    step:   scan | signal | debate | risk | stage | execute
    status: ok | skip | blocked | error
    """
    try:
        httpx.post(
            f"{backend_base}/api/brain/activity",
            json={"symbol": symbol, "step": step, "status": status, "detail": detail},
            timeout=2,
        )
    except Exception:
        pass
