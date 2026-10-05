from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.settings import ChannelSettings, SettingsStore

CHANNEL_PROVIDERS = {"telegram": "Telegram"}


async def lock_channel_provider(
    db: AsyncSession, provider: str, *, shared: bool = False
) -> None:
    function = "pg_advisory_xact_lock_shared" if shared else "pg_advisory_xact_lock"
    await db.execute(
        text(f"SELECT {function}(hashtextextended(:scope, 0))"),
        {"scope": f"channel-provider:{provider}"},
    )


async def channel_provider_allowed(
    db: AsyncSession, provider: str, *, lock: bool = False
) -> bool:
    if lock:
        await lock_channel_provider(db, provider, shared=True)
    settings = await SettingsStore.load(db, ChannelSettings)
    return settings.enabled and provider in settings.allowed_providers
