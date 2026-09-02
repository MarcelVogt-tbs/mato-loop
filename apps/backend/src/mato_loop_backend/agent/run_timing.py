from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from mato_loop_backend.models import AgentRun


def transition_run_status(
    run: AgentRun, status: str, *, now: datetime | None = None
) -> None:
    if run.status == status:
        return
    timestamp = now or datetime.now(UTC)
    if run.status == "running" and status != "running":
        if run.active_started_at is not None:
            elapsed_ms = round((timestamp - run.active_started_at).total_seconds() * 1_000)
            run.active_duration_ms = (run.active_duration_ms or 0) + max(0, elapsed_ms)
        run.active_started_at = None
    elif run.status != "running" and status == "running":
        run.active_started_at = timestamp
    run.active_timing_revision = (run.active_timing_revision or 0) + 1
    run.status = status


def run_timing_payload(
    run: AgentRun, *, now: datetime | None = None
) -> dict[str, Any]:
    duration_ms = max(0, run.active_duration_ms or 0)
    if run.status == "running" and run.active_started_at is not None:
        elapsed_ms = round(
            ((now or datetime.now(UTC)) - run.active_started_at).total_seconds() * 1_000
        )
        duration_ms += max(0, elapsed_ms)
    return {
        "activeDurationMs": duration_ms,
        "activeDurationRunning": run.status == "running",
        "activeTimingRevision": max(0, run.active_timing_revision or 0),
    }
