from mato_loop_backend.integrations.bookstack import (
    BOOKSTACK_CREDENTIAL_KIND,
    BookStackConfig,
    bookstack_error_code,
    load_bookstack_resources,
)
from mato_loop_backend.integrations.caldav import (
    CALDAV_CREDENTIAL_KIND,
    CalDavConfig,
    caldav_error_code,
    load_caldav_resources,
)
from mato_loop_backend.integrations.evidence import evidence_source_id
from mato_loop_backend.integrations.mail import (
    MAIL_CREDENTIAL_KIND,
    MailConfig,
    mail_error_code,
)
from mato_loop_backend.integrations.mealie import (
    MEALIE_CREDENTIAL_KIND,
    MealieConfig,
    mealie_error_code,
)
from mato_loop_backend.integrations.mealie_client import MealieClient, MealieError
from mato_loop_backend.integrations.opencode import (
    OPENCODE_CREDENTIAL_KIND,
    OpenCodeConfig,
    OpenCodeIntegrationService,
    OpenCodeSettingsError,
    open_code_client,
)
from mato_loop_backend.integrations.opencode_client import OpenCodeError
from mato_loop_backend.integrations.registry import effective_execution_integration_settings
from mato_loop_backend.integrations.search import (
    SearchIntegrationError,
    effective_search_providers,
    normalize_search_tool_settings,
    search_available,
    search_provider_options,
    search_web,
    search_with_key,
    search_with_provider,
)
from mato_loop_backend.integrations.service import (
    BookStackIntegrationService,
    CalDavIntegrationService,
    IntegrationSettingsError,
    MailIntegrationService,
    MealieIntegrationService,
    OpenScadIntegrationService,
    SearchIntegrationService,
)
from mato_loop_backend.integrations.webfetch import (
    PdfVisionHandler,
    WebFetchBytesResult,
    WebFetchError,
    WebFetchImage,
    WebFetchResult,
    canonicalize_url,
    fetch_web,
    normalize_source_title,
    request_web_bytes,
)
from mato_loop_backend.integrations.youtube_transcript import (
    TranscriptError,
    TranscriptResult,
    canonicalize_youtube_video_url,
    fetch_youtube_transcript,
)

__all__ = [
    "SearchIntegrationError",
    "BOOKSTACK_CREDENTIAL_KIND",
    "CALDAV_CREDENTIAL_KIND",
    "MEALIE_CREDENTIAL_KIND",
    "MAIL_CREDENTIAL_KIND",
    "BookStackConfig",
    "BookStackIntegrationService",
    "CalDavConfig",
    "CalDavIntegrationService",
    "MealieClient",
    "MealieConfig",
    "MealieError",
    "MealieIntegrationService",
    "MailConfig",
    "MailIntegrationService",
    "OpenScadIntegrationService",
    "OPENCODE_CREDENTIAL_KIND",
    "OpenCodeConfig",
    "OpenCodeError",
    "OpenCodeIntegrationService",
    "OpenCodeSettingsError",
    "IntegrationSettingsError",
    "PdfVisionHandler",
    "SearchIntegrationService",
    "TranscriptError",
    "TranscriptResult",
    "WebFetchError",
    "WebFetchBytesResult",
    "WebFetchImage",
    "WebFetchResult",
    "canonicalize_url",
    "canonicalize_youtube_video_url",
    "bookstack_error_code",
    "caldav_error_code",
    "mealie_error_code",
    "mail_error_code",
    "evidence_source_id",
    "effective_search_providers",
    "effective_execution_integration_settings",
    "normalize_search_tool_settings",
    "search_available",
    "search_provider_options",
    "search_web",
    "search_with_key",
    "search_with_provider",
    "fetch_web",
    "fetch_youtube_transcript",
    "normalize_source_title",
    "load_bookstack_resources",
    "load_caldav_resources",
    "request_web_bytes",
    "open_code_client",
]
