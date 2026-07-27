from __future__ import annotations

import asyncio
import base64
import logging
import math
from collections.abc import Awaitable, Callable
from typing import Any, Literal
from uuid import UUID

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.attachments import (
    MAX_ATTACHMENT_BYTES,
    AttachmentError,
    AttachmentStorage,
    AttachmentStorageError,
    validate_image,
)
from mato_loop_backend.models import Credential
from mato_loop_backend.privacy import (
    PseudonymizationError,
    RequestPseudonymizer,
    StreamingPseudonymRestorer,
)
from mato_loop_backend.providers import codex_oauth, lemonade, openai_compatible
from mato_loop_backend.providers.base import (
    ProviderError,
    ProviderMedia,
    ProviderRequest,
    ProviderResult,
    ReasoningDeltaCallback,
    select_provider_images,
)
from mato_loop_backend.providers.openai_compatible import (
    DiscoveredModel,
    chat_completions,
    responses_api,
)
from mato_loop_backend.security import read_credential, store_credential
from mato_loop_backend.settings.models import (
    LlmProviderConfig,
    ModelCapability,
    ModelConfig,
    TrustLevel,
    effective_model_pseudonymization,
    effective_model_trust,
)

logger = logging.getLogger(__name__)

_IMAGE_MIME_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}


def _pseudonymization_provider_error(exc: PseudonymizationError) -> ProviderError:
    return ProviderError("Local pseudonymization failed", code=str(exc))


async def _refresh_codex_credential(
    db: AsyncSession,
    credential: Credential,
    payload: dict[str, Any],
) -> dict[str, Any]:
    try:
        refreshed = await codex_oauth.refresh_token(payload)
    except httpx.HTTPStatusError as exc:
        code = (
            "provider.authenticationFailed"
            if exc.response.status_code in {400, 401, 403}
            else None
        )
        raise ProviderError(
            "ChatGPT Codex token refresh failed",
            status_code=exc.response.status_code,
            code=code,
        ) from exc
    except httpx.HTTPError as exc:
        raise ProviderError(
            "ChatGPT Codex is unavailable", code="provider.unavailable"
        ) from exc
    except (KeyError, TypeError, ValueError) as exc:
        raise ProviderError(
            "ChatGPT Codex credential is invalid",
            code="provider.authenticationFailed",
        ) from exc
    if refreshed != payload:
        await store_credential(
            db,
            kind=credential.kind,
            label=credential.label,
            payload=refreshed,
            metadata=credential.metadata_json,
            credential=credential,
        )
        await db.commit()
    return refreshed


def _boolean_option(config: dict[str, Any], name: str, default: bool) -> bool:
    value = config.get(name)
    return value if isinstance(value, bool) else default


def _positive_int_option(config: dict[str, Any], name: str) -> int | None:
    value = config.get(name)
    return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else None


def _temperature_option(config: dict[str, Any]) -> float | None:
    value = config.get("temperature")
    if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value):
        return float(value)
    return None


def _read_image_data_url(storage: AttachmentStorage, attachment: dict[str, Any]) -> str:
    path = storage.path(UUID(attachment["session_id"]), attachment["storage_path"])
    with path.open("rb") as file:
        data = file.read(MAX_ATTACHMENT_BYTES + 1)
    if len(data) > MAX_ATTACHMENT_BYTES or len(data) != attachment["size_bytes"]:
        raise AttachmentStorageError("Invalid attachment file size")
    validated = validate_image(data, attachment["mime_type"])
    if validated.sha256 != attachment["sha256"]:
        raise AttachmentStorageError("Invalid attachment file content")
    encoded = base64.b64encode(data).decode("ascii")
    return f"data:{validated.mime_type};base64,{encoded}"


async def _provider_projection(
    messages: list[dict[str, Any]],
    *,
    supports_image_input: bool,
    input_trust_level: TrustLevel,
    target_trust_level: TrustLevel,
) -> tuple[list[dict[str, Any]], list[ProviderMedia]]:
    projected_messages: list[dict[str, Any]] = []
    for message in messages:
        projected = dict(message)
        projected.pop("attachments", None)
        projected_messages.append(projected)

    if not supports_image_input:
        return projected_messages, []

    selected = select_provider_images(
        messages,
        input_trust_level=input_trust_level.value,
        target_trust_level=target_trust_level.value,
    )
    media = [
        ProviderMedia(
            message_index=item.message_index,
            mime_type=str(item.attachment.get("mime_type") or "image"),
            status="unavailable",
        )
        for item in selected
    ]
    if not selected:
        return projected_messages, media
    if not any(item.allow_raw for item in selected):
        return projected_messages, media
    storage = AttachmentStorage()

    async def materialize(position: int, attachment: dict[str, Any]) -> None:
        mime_type = attachment.get("mime_type")
        size_bytes = attachment.get("size_bytes")
        sha256 = attachment.get("sha256")
        session_id = attachment.get("session_id")
        storage_path = attachment.get("storage_path")
        if (
            mime_type not in _IMAGE_MIME_TYPES
            or not isinstance(size_bytes, int)
            or isinstance(size_bytes, bool)
            or not 0 < size_bytes <= MAX_ATTACHMENT_BYTES
            or not isinstance(sha256, str)
            or len(sha256) != 64
            or not isinstance(session_id, str)
            or not isinstance(storage_path, str)
        ):
            return
        try:
            data_url = await asyncio.to_thread(_read_image_data_url, storage, attachment)
        except (AttachmentError, AttachmentStorageError, OSError, ValueError):
            return
        media[position].data_url = data_url
        media[position].status = "ready"

    for position, item in enumerate(selected):
        if item.allow_raw:
            await materialize(position, item.attachment)
    unavailable_count = sum(item.status != "ready" for item in media)
    if unavailable_count:
        logger.warning(
            "%s attachment file(s) could not be sent to the provider", unavailable_count
        )
    return projected_messages, media


async def _with_retries(
    operation: Callable[[], Awaitable[ProviderResult]],
    can_retry: Callable[[], bool] | None = None,
) -> ProviderResult:
    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            return await operation()
        except ProviderError as exc:
            transient = exc.status_code == 429 or (
                exc.status_code is not None and exc.status_code >= 500
            )
            if not transient or attempt == max_retries or (can_retry and not can_retry()):
                raise
            delay = exc.retry_after if exc.retry_after is not None else 0.25 * (2**attempt)
        except httpx.RequestError as exc:
            if attempt == max_retries or (can_retry and not can_retry()):
                raise ProviderError(
                    "Provider network request failed", code="provider.unavailable"
                ) from exc
            delay = 0.25 * (2**attempt)
        logger.info("Retrying transient provider request (attempt %s)", attempt + 2)
        await asyncio.sleep(min(delay, 30.0))
    raise ProviderError("Provider request failed", code="provider.requestFailed")


class ProviderService:
    @staticmethod
    async def discover_models(
        db: AsyncSession,
        *,
        provider: LlmProviderConfig,
        timeout: float = 30,
    ) -> list[DiscoveredModel]:
        credential = (
            await db.get(Credential, provider.credential_id, populate_existing=True)
            if provider.credential_id
            else None
        )
        payload = read_credential(credential) if credential else {}
        if provider.adapter_type == "openai-codex":
            if credential is None:
                raise ProviderError(
                    "ChatGPT Codex is not connected", code="provider.notConnected"
                )
            refreshed = await _refresh_codex_credential(db, credential, payload)
            return await codex_oauth.discover_models(
                access_token=refreshed["access"],
                account_id=refreshed.get("account_id"),
                timeout=timeout,
            )
        if provider.adapter_type == "lemonade":
            base_url = str(provider.config.get("base_url") or "").rstrip("/")
            if not base_url:
                raise ProviderError(
                    "Provider base URL is missing", code="provider.configurationInvalid"
                )
            return await lemonade.discover_models(
                base_url=base_url,
                api_key=payload.get("api_key"),
                timeout=timeout,
            )
        if provider.adapter_type != "openai-compatible":
            raise ProviderError(
                "Provider does not support model discovery",
                code="provider.discoveryUnsupported",
            )
        base_url = str(provider.config.get("base_url") or "").rstrip("/")
        if not base_url:
            raise ProviderError(
                "Provider base URL is missing", code="provider.configurationInvalid"
            )
        return await openai_compatible.discover_models(
            base_url=base_url,
            api_key=payload.get("api_key"),
            timeout=timeout,
        )

    @staticmethod
    async def complete(
        db: AsyncSession,
        *,
        provider: LlmProviderConfig,
        model: ModelConfig,
        input_trust_level: TrustLevel,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        tool_choice: Literal["auto", "required", "none"] = "auto",
        on_delta: Callable[[str], Awaitable[None]] | None = None,
        on_reasoning: ReasoningDeltaCallback | None = None,
        timeout: float = 300,
        max_output_tokens: int | None = None,
        target_trust_level: TrustLevel | None = None,
        pseudonymization_enabled: bool | None = None,
    ) -> ProviderResult:
        if model.provider_id != provider.id:
            raise ProviderError(
                "Model and provider do not belong together",
                code="provider.configurationInvalid",
            )
        if ModelCapability.CHAT not in model.capabilities:
            raise ProviderError(
                "Model does not support chat",
                code="provider.modelCapabilityUnavailable",
            )
        if provider.adapter_type not in {"openai-compatible", "openai-codex", "lemonade"}:
            raise ProviderError(
                "Unsupported provider adapter", code="provider.configurationInvalid"
            )
        model_trust_level = target_trust_level or effective_model_trust(provider, model)
        use_pseudonymization = (
            model_trust_level == TrustLevel.PUBLIC
            and (
                pseudonymization_enabled
                if pseudonymization_enabled is not None
                else effective_model_pseudonymization(provider, model)
            )
        )
        credential = (
            await db.get(Credential, provider.credential_id, populate_existing=True)
            if provider.credential_id
            else None
        )
        payload = read_credential(credential) if credential else {}
        is_codex = provider.adapter_type == "openai-codex"
        endpoint = (
            codex_oauth.CODEX_RESPONSES_URL
            if is_codex
            else str(provider.config.get("base_url") or "").rstrip("/")
        )
        continuation_scope = {
            "adapterType": provider.adapter_type,
            "providerId": str(provider.id),
            "modelConfigId": str(model.id),
            "credentialId": str(provider.credential_id) if provider.credential_id else None,
            "endpoint": endpoint,
            "trustLevel": model_trust_level.value,
            "pseudonymization": "enabled" if use_pseudonymization else "disabled",
        }
        provider_messages, media = await _provider_projection(
            messages,
            supports_image_input=model.supports_image_input,
            input_trust_level=input_trust_level,
            target_trust_level=model_trust_level,
        )
        pseudonymizer = RequestPseudonymizer() if use_pseudonymization else None
        if pseudonymizer is not None:
            try:
                provider_messages = await asyncio.to_thread(
                    pseudonymizer.protect_messages, provider_messages
                )
            except PseudonymizationError as exc:
                raise _pseudonymization_provider_error(exc) from exc
        request = ProviderRequest(
            model=(
                model.model_id.removeprefix("openai-codex/") if is_codex else model.model_id
            ),
            messages=provider_messages,
            tools=tools,
            tool_choice=tool_choice,
            stream=model.supports_streaming,
            supports_parallel_tool_calls=(
                model.supports_tools and model.supports_parallel_tool_calls
            ),
            max_output_tokens=(
                None
                if is_codex
                else min(max_output_tokens or model.max_output_tokens, model.max_output_tokens)
            ),
            temperature=None if is_codex else _temperature_option(model.config),
            timeout=timeout,
            reasoning_summary=is_codex or model.reasoning_summary,
            continuation_scope=continuation_scope,
            media=media,
        )
        stream_started = False
        delta_restorer = (
            StreamingPseudonymRestorer(pseudonymizer)
            if pseudonymizer is not None and on_delta is not None
            else None
        )
        reasoning_restorers: dict[str, StreamingPseudonymRestorer] = {}

        async def tracked_delta(text: str) -> None:
            nonlocal stream_started
            stream_started = True
            if on_delta:
                try:
                    restored = delta_restorer.feed(text) if delta_restorer else text
                except PseudonymizationError as exc:
                    raise _pseudonymization_provider_error(exc) from exc
                if restored:
                    await on_delta(restored)

        async def tracked_reasoning(text: str, kind: str) -> None:
            nonlocal stream_started
            stream_started = True
            if on_reasoning:
                restorer = reasoning_restorers.get(kind)
                if pseudonymizer is not None and restorer is None:
                    restorer = StreamingPseudonymRestorer(pseudonymizer)
                    reasoning_restorers[kind] = restorer
                try:
                    restored = restorer.feed(text) if restorer else text
                except PseudonymizationError as exc:
                    raise _pseudonymization_provider_error(exc) from exc
                if restored:
                    await on_reasoning(restored, kind)

        delta_callback = tracked_delta if on_delta else None
        reasoning_callback = tracked_reasoning if on_reasoning else None

        async def finalize(result: ProviderResult) -> ProviderResult:
            try:
                if delta_restorer is not None and on_delta is not None:
                    if remaining := delta_restorer.flush():
                        await on_delta(remaining)
                if on_reasoning is not None:
                    for kind, restorer in reasoning_restorers.items():
                        if remaining := restorer.flush():
                            await on_reasoning(remaining, kind)
                if pseudonymizer is not None:
                    result.content = pseudonymizer.restore_text(result.content)
                    for call in result.tool_calls:
                        call.arguments = pseudonymizer.restore_value(call.arguments)
                    if result.reasoning is not None:
                        result.reasoning.text = pseudonymizer.restore_text(
                            result.reasoning.text
                        )
                    for key in (
                        "responses_output_items",
                        "reasoning_encrypted_content",
                        "chat_reasoning_details",
                    ):
                        result.provider_data.pop(key, None)
                    return result
            except PseudonymizationError as exc:
                raise _pseudonymization_provider_error(exc) from exc
            if (
                result.provider_data.get("responses_output_items")
                or result.provider_data.get("reasoning_encrypted_content")
                or result.provider_data.get("chat_reasoning_details")
            ):
                result.provider_data["continuation_scope"] = continuation_scope
            return result

        def can_retry() -> bool:
            return not stream_started

        if is_codex:
            if credential is None:
                raise ProviderError(
                    "ChatGPT Codex is not connected", code="provider.notConnected"
                )
            refreshed = await _refresh_codex_credential(db, credential, payload)
            headers = {
                "Authorization": f"Bearer {refreshed['access']}",
                "chatgpt-account-id": refreshed.get("account_id") or "",
                "OpenAI-Beta": "responses=experimental",
                "originator": "mato-loop",
                "User-Agent": "MatoLoop/0.1",
                "Accept": "text/event-stream" if request.stream else "application/json",
                "Content-Type": "application/json",
            }

            async def execute_codex() -> ProviderResult:
                return await responses_api(
                    url=codex_oauth.CODEX_RESPONSES_URL,
                    headers=headers,
                    request=request,
                    on_delta=delta_callback,
                    on_reasoning=reasoning_callback,
                )

            return await finalize(await _with_retries(execute_codex, can_retry))

        base_url = str(provider.config.get("base_url") or "").rstrip("/")
        if not base_url:
            raise ProviderError(
                "Provider base URL is missing", code="provider.configurationInvalid"
            )
        api_key = payload.get("api_key")
        api_type = (
            "chat_completions"
            if provider.adapter_type == "lemonade"
            else str(provider.config.get("api_type") or "chat_completions")
        )
        if api_type == "responses":
            headers = {"Content-Type": "application/json"}
            if api_key:
                headers["Authorization"] = f"Bearer {api_key}"

            async def execute_responses() -> ProviderResult:
                return await responses_api(
                    url=f"{base_url}/responses",
                    headers=headers,
                    request=request,
                    on_delta=delta_callback,
                    on_reasoning=reasoning_callback,
                )

            return await finalize(await _with_retries(execute_responses, can_retry))

        async def execute_chat() -> ProviderResult:
            return await chat_completions(
                base_url=base_url,
                api_key=api_key,
                request=request,
                on_delta=delta_callback,
                on_reasoning=reasoning_callback,
            )

        return await finalize(await _with_retries(execute_chat, can_retry))
