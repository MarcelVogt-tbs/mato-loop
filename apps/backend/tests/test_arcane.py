import json
from contextlib import asynccontextmanager
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from pydantic import ValidationError

import mato_loop_backend.integrations.arcane.adapter as arcane_module
from mato_loop_backend.integrations.arcane.adapter import (
    ArcaneAction,
    ArcaneDelete,
    ArcaneGet,
    ArcaneList,
    _binding,
    _delete_permission,
    _public,
    arcane_action,
)
from mato_loop_backend.integrations.arcane.client import ArcaneClient, ArcaneError, segment
from mato_loop_backend.settings import IntegrationInstance, IntegrationSettings


@pytest.mark.asyncio
async def test_arcane_request_limits_origin_and_requires_stream_completion():
    requests = []

    def handler(request):
        requests.append(request)
        if request.url.path.endswith("/up"):
            return httpx.Response(200, content=b'{"activityId":"run-1"}\n{"log":"secret"}\n{"done":true}\n')
        return httpx.Response(200, json={"success": True, "data": [{"id": "0"}], "pagination": {"total": 1}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as transport:
        async with ArcaneClient("https://arcane.example.test/panel", "test-key", client=transport) as client:
            data, _ = await client.request("GET", "/environments", params={"limit": 1})
            assert data == [{"id": "0"}]
            assert await client.request("POST", "/environments/0/projects/x/up", streamed=True) == {
                "completed": True, "activityId": "run-1"
            }
    assert requests[0].url.path == "/panel/api/environments"
    assert requests[0].headers["X-Api-Key"] == "test-key"
    assert requests[1].url.path == "/panel/api/environments/0/projects/x/up"

    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b'{"log":"working"}\n'))) as transport:
        async with ArcaneClient("https://arcane.example.test", "test-key", client=transport) as client:
            with pytest.raises(ArcaneError, match="integration.outcomeUnknown"):
                await client.request("POST", "/environments/0/projects/x/up", streamed=True)


def test_arcane_actions_and_permissions_are_target_bound():
    instance = IntegrationInstance(name="Host", integration_type="infrastructure", adapter_type="arcane",
                                   config={"base_url": "https://arcane.example.test"}, credential_id=uuid4())
    revision = json.dumps(instance.model_dump(mode="json"), ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    context = SimpleNamespace(bound_options={"integration_id": str(instance.id), "binding_revision": revision},
                              integration_settings=IntegrationSettings(instances=[instance]))
    arguments = ArcaneDelete(integration_id=instance.id, environment_id="0", kind="volumes", resource_id="db/data")
    permission = _delete_permission(arguments, context)[0]
    assert permission.capability == "arcane.delete"
    assert permission.policy_resource_key == "0"
    assert "db/data" in permission.summary
    assert segment("db/data") == "db%2Fdata"
    context.bound_options["binding_revision"] = "stale"
    with pytest.raises(ValueError):
        _binding(context, instance.id)
    with pytest.raises(ValidationError):
        ArcaneAction(integration_id=instance.id, environment_id="0", action="up", name="other")
    with pytest.raises(ValidationError):
        ArcaneList(integration_id=instance.id, environment_id="../other", kind="containers")
    with pytest.raises(ValidationError):
        ArcaneGet(integration_id=instance.id, environment_id="0", kind="containers", resource_id="x", view="updates")


def test_arcane_does_not_release_remote_secrets():
    assert _public({"id": "x", "name": "app", "config": {"env": ["KEY=secret"]},
                    "apiKey": "secret", "mounts": [{"source": "/sensitive"}]}, "containers") == {
        "id": "x", "name": "app"
    }


@pytest.mark.asyncio
async def test_arcane_actions_use_only_named_routes_and_explicit_fields(monkeypatch):
    requests = []

    async def request(method, path, *, body=None, streamed=False):
        requests.append((method, path, body, streamed))
        return ({"completed": True, "activityId": "a"} if streamed else ({"activityId": "a"}, None))

    @asynccontextmanager
    async def selected_client(_args, _context):
        yield SimpleNamespace(request=request)

    monkeypatch.setattr(arcane_module, "_client", selected_client)
    identifier = uuid4()
    context = SimpleNamespace()
    for arguments in (
        ArcaneAction(integration_id=identifier, environment_id="0", action="create_container", name="app", image="nginx:stable"),
        ArcaneAction(integration_id=identifier, environment_id="0", action="auto_update", resource_id="abc", enabled=False),
        ArcaneAction(integration_id=identifier, environment_id="0", action="build", resource_id="proj"),
    ):
        assert not (await arcane_action(arguments, context)).is_error
    assert requests == [
        ("POST", "/environments/0/containers", {"name": "app", "image": "nginx:stable"}, False),
        ("PUT", "/environments/0/containers/abc/auto-update", {"enabled": False}, False),
        ("POST", "/environments/0/projects/proj/build", None, True),
    ]


@pytest.mark.asyncio
async def test_arcane_refuses_redirects_and_unbounded_read_responses():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(302, headers={"Location": "https://other.test"}))) as transport:
        async with ArcaneClient("https://arcane.example.test", "key", client=transport) as client:
            with pytest.raises(ArcaneError, match="integration.unavailable"):
                await client.request("GET", "/environments")
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"x" * (256 * 1024 + 1)))) as transport:
        async with ArcaneClient("https://arcane.example.test", "key", client=transport) as client:
            with pytest.raises(ArcaneError, match="integration.responseTooLarge"):
                await client.request("GET", "/environments")
