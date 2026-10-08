"""Context projection setup shared by context and file-output tests."""

from mato_loop_backend.agent.context import ContextManager
from mato_loop_backend.providers.service import ProviderService
from mato_loop_backend.settings.models import LlmProviderConfig, TrustLevel


def _request_estimator(model, *, tools=None, target_trust_level=TrustLevel.PUBLIC, **options):
    provider = LlmProviderConfig(
        id=model.provider_id, name="Test", adapter_type="openai-compatible",
        pseudonymization_enabled=False,
    )

    async def estimate(messages):
        prepared = await ProviderService.prepare_request(
            provider=provider, model=model, messages=messages, tools=tools or [],
            target_trust_level=target_trust_level, **options,
        )
        return prepared.estimated_input_tokens

    return estimate


async def _build_context(db, **kwargs):
    return await ContextManager.build(
        db, **kwargs, estimate_request=_request_estimator(
            kwargs["model"], tools=kwargs["tool_schemas"],
            target_trust_level=kwargs.get("target_trust_level", TrustLevel.PUBLIC),
        ),
    )
