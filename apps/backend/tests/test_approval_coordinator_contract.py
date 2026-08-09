from uuid import uuid4

import pytest

from mato_loop_backend.agent.coordinator import ApprovalCoordinator


async def test_approval_coordinator_registers_and_resolves_waiter() -> None:
    coordinator = ApprovalCoordinator()
    approval_id = uuid4()
    waiter = coordinator.register(approval_id)

    assert coordinator.is_waiting(approval_id) is True
    with pytest.raises(RuntimeError, match="approval waiter already exists"):
        coordinator.register(approval_id)

    assert coordinator.resolve(approval_id, "allow_once") is True
    assert await waiter == "allow_once"
    assert coordinator.is_waiting(approval_id) is False
    assert coordinator.resolve(approval_id, "deny") is False


async def test_approval_coordinator_discards_waiter() -> None:
    coordinator = ApprovalCoordinator()
    approval_id = uuid4()
    waiter = coordinator.register(approval_id)

    coordinator.discard(approval_id)

    assert coordinator.is_waiting(approval_id) is False
    assert coordinator.resolve(approval_id, "allow_once") is False
    waiter.cancel()


def test_approval_coordinator_ignores_unknown_ids() -> None:
    coordinator = ApprovalCoordinator()
    approval_id = uuid4()

    assert coordinator.is_waiting(approval_id) is False
    assert coordinator.resolve(approval_id, "deny") is False
    coordinator.discard(approval_id)


async def test_failed_resolution_can_be_retried() -> None:
    coordinator = ApprovalCoordinator()
    approval_id = uuid4()
    waiter = coordinator.register(approval_id)

    assert coordinator.claim_resolution(approval_id) is True
    coordinator.release_resolution(approval_id)
    assert coordinator.resolve(approval_id, "allow_session") is True
    assert await waiter == "allow_session"
