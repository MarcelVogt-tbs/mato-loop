from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mato_loop_backend.db.base import Base


class SettingRecord(Base):
    __tablename__ = "settings"
    __table_args__ = (
        Index(
            "uq_settings_global_key",
            "key",
            unique=True,
            postgresql_where=text("user_id IS NULL"),
        ),
        Index(
            "uq_settings_user_key",
            "user_id",
            "key",
            unique=True,
            postgresql_where=text("user_id IS NOT NULL"),
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    key: Mapped[str] = mapped_column(String(120), nullable=False)
    user_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=True, index=True
    )
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )
