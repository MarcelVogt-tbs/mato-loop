from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx

from mato_loop_backend.providers.base import ProviderError, ProviderRequest
from mato_loop_backend.providers.openai_compatible import DiscoveredModel

ACCOUNT_URL = "https://api.github.com/copilot_internal/user"
DEFAULT_API_URL = "https://api.individual.githubcopilot.com"


def headers(token: str, request: ProviderRequest | None = None) -> dict[str, str]:
    result = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Copilot-Integration-Id": "copilot-developer-cli",
        "OpenAI-Organization": "github-copilot",
    }
    if request is not None:
        last_role = request.messages[-1].get("role") if request.messages else None
        result["X-Initiator"] = "agent" if last_role in {"assistant", "tool"} else "user"
        if any(item.modality == "image" and item.data_url for item in request.media):
            result["Copilot-Vision-Request"] = "true"
        if request.stream:
            result["Accept"] = "text/event-stream"
    return result


def _object(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


async def _fetch_json(client: httpx.AsyncClient, url: str, token: str) -> Any:
    response = await client.get(url, headers=headers(token), follow_redirects=False)
    if not response.is_success:
        raise ProviderError("GitHub Copilot request failed", status_code=response.status_code)
    try:
        return response.json()
    except ValueError:
        raise ProviderError("Invalid GitHub Copilot response") from None


async def resolve_api_url(client: httpx.AsyncClient, token: str) -> str:
    account = await _fetch_json(client, ACCOUNT_URL, token)
    if not isinstance(account, dict):
        raise ProviderError("Invalid GitHub Copilot account response")
    endpoint = _object(account.get("endpoints")).get("api")
    if endpoint is None:
        return DEFAULT_API_URL
    if isinstance(endpoint, str) and not any(char.isspace() for char in endpoint):
        try:
            url = urlsplit(endpoint)
            host = url.hostname or ""
            if (
                url.scheme == "https"
                and url.netloc.lower() == host
                and host in {
                    "api.githubcopilot.com",
                    "api.individual.githubcopilot.com",
                    "api.business.githubcopilot.com",
                    "api.enterprise.githubcopilot.com",
                    "copilot-proxy.githubusercontent.com",
                }
                and url.path in {"", "/"}
                and not url.query
                and not url.fragment
            ):
                return f"https://{host}"
        except ValueError:
            pass
    raise ProviderError("Untrusted GitHub Copilot API endpoint")


def _model(entry: Any) -> DiscoveredModel | None:
    entry = _object(entry)
    model_id = entry.get("id")
    if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 300:
        return None
    policy = str(_object(entry.get("policy")).get("state", "")).lower()
    if entry.get("model_picker_enabled") is not True or policy in {"disabled", "unconfigured"}:
        return None
    capabilities = _object(entry.get("capabilities"))
    supports = _object(capabilities.get("supports"))
    if supports.get("tool_calls") is not True or supports.get("streaming") is False:
        return None
    routes = entry.get("supported_endpoints")
    preferred = ["/responses", "/chat/completions"] if model_id.startswith(("gpt-", "o1", "o3", "o4")) else ["/chat/completions", "/responses"]
    if isinstance(routes, list):
        route = next((path for path in preferred if path in routes), None)
    else:
        route = None if model_id.startswith("claude-") else preferred[0]
    if route is None:
        return None
    limits = _object(capabilities.get("limits"))
    context_length = next((
        value for key in ("max_context_window_tokens", "max_prompt_tokens")
        if type(value := limits.get(key)) is int and 2 <= value <= 10_000_000
    ), None)
    name = entry.get("name")
    return DiscoveredModel(
        id=model_id,
        name=name[:160] if isinstance(name, str) else None,
        copilot_api_type="responses" if route == "/responses" else "chat_completions",
        supports_tools=True,
        supports_parallel_tool_calls=supports.get("parallel_tool_calls") is True,
        input_modalities=["text", "image"] if supports.get("vision") is True else ["text"],
        context_length=context_length,
    )


async def account_api_url(token: str, timeout: float) -> str:
    async with httpx.AsyncClient(timeout=timeout) as client:
        return await resolve_api_url(client, token)


async def discover_models(token: str, timeout: float) -> list[DiscoveredModel]:
    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            base_url = await resolve_api_url(client, token)
            payload = await _fetch_json(client, f"{base_url}/models", token)
    except httpx.RequestError:
        raise ProviderError("GitHub Copilot is unavailable", code="provider.unavailable") from None
    rows = _object(payload).get("data")
    if not isinstance(rows, list):
        raise ProviderError("Invalid GitHub Copilot model catalog")
    return [model for entry in rows if (model := _model(entry)) is not None]
