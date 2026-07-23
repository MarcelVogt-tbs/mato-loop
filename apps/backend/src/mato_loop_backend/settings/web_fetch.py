from mato_loop_backend.settings.models import (
    ModelConfig,
    ModelWebFetchPolicy,
    ToolSettings,
    WebFetchPolicy,
)


def resolve_web_fetch_policy(
    settings: ToolSettings,
    model: ModelConfig,
) -> WebFetchPolicy:
    if not settings.web_fetch.enabled:
        return WebFetchPolicy.DISABLED
    if model.webfetch_policy == ModelWebFetchPolicy.INHERIT:
        return settings.web_fetch.default_policy
    return WebFetchPolicy(model.webfetch_policy.value)
