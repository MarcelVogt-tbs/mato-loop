from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from mato_loop_backend.models import UserAccount
from mato_loop_backend.security import verify_password


class LocalAuthProvider:
    provider_id = "local"

    @staticmethod
    async def authenticate(
        db: AsyncSession, username: str, password: str
    ) -> UserAccount | None:
        user = await db.scalar(
            select(UserAccount).where(
                func.lower(UserAccount.username) == func.lower(username.strip())
            )
        )
        if user is None or user.password_hash is None:
            return None
        return user if verify_password(password, user.password_hash) else None
