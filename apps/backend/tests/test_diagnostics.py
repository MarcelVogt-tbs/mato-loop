import asyncio
from unittest.mock import AsyncMock

import mato_loop_backend.diagnostics.service as diagnostics_service


def test_diagnostic_payload_is_bounded() -> None:
    payload = diagnostics_service._bounded_payload({"context": "x" * diagnostics_service.MAX_PAYLOAD_BYTES})

    assert payload["truncated"] is True
    assert payload["originalBytes"] > diagnostics_service.MAX_PAYLOAD_BYTES
    assert payload["availableKeys"] == ["context"]


async def test_diagnostic_schedule_runs_in_background(monkeypatch) -> None:
    record = AsyncMock()
    monkeypatch.setattr(diagnostics_service, "record_diagnostic_event", record)

    diagnostics_service.schedule_diagnostic_event("user", "test", component="test")
    await asyncio.sleep(0)

    record.assert_awaited_once()
    assert record.await_args.args == ("user", "test")
    assert record.await_args.kwargs["component"] == "test"
    assert record.await_args.kwargs["occurred_at"].tzinfo is not None
