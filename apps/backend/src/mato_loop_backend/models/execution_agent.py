from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, Integer, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from mato_loop_backend.db.base import Base


class ExecutionAgent(Base):
    __tablename__ = "execution_agents"
    __table_args__ = (
        CheckConstraint(
            "(registration_token_hash IS NULL) = (registration_expires_at IS NULL)",
            name="registration_complete",
        ),
        CheckConstraint(
            "(token_hash IS NULL) = (token_is_legacy IS NULL)",
            name="durable_token_classified",
        ),
        CheckConstraint(
            "token_hash IS NULL OR registration_token_hash IS NULL",
            name="credential_state_exclusive",
        ),
        *(
            CheckConstraint(
                f"{name} IN ('auto', 'ask', 'deny')",
                name=f"{name}_valid",
            )
            for name in (
                "home_filesystem_policy",
                "external_filesystem_policy",
            )
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(
        ForeignKey("user_accounts.id", ondelete="CASCADE"), nullable=False, index=True
    )
    alias: Mapped[str] = mapped_column(String(160), nullable=False)
    computer_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    agent_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    platform: Mapped[str | None] = mapped_column(String(120), nullable=True)
    token_hash: Mapped[str | None] = mapped_column(String(64), nullable=True, unique=True)
    token_is_legacy: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    registration_token_hash: Mapped[str | None] = mapped_column(
        String(64), nullable=True, unique=True
    )
    registration_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    operations: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    maintenance: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    auto_update_enabled: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sudo_credential_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("credentials.id", ondelete="SET NULL"), nullable=True
    )
    home_directory: Mapped[str | None] = mapped_column(String(4096), nullable=True)
    home_filesystem_policy: Mapped[str] = mapped_column(
        String(20), nullable=False, default="auto", server_default=text("'auto'")
    )
    external_filesystem_policy: Mapped[str] = mapped_column(
        String(20), nullable=False, default="ask", server_default=text("'ask'")
    )
    home_shell_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    external_shell_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    last_connected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    @property
    def display_name(self) -> str:
        return (self.alias or "").strip() or (self.computer_name or "").strip() or f"Computer {str(self.id)[:8]}"
