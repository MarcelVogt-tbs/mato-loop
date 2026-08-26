"""extract user profile from memories

Revision ID: 1a04e736b206
Revises: 57bb810fcc64
Create Date: 2026-08-26 13:02:40.479751
"""

import hashlib
import re
from typing import Any, Sequence, Union
from uuid import uuid4

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision: str = "1a04e736b206"
down_revision: Union[str, None] = "57bb810fcc64"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

PROFILE_FIELDS = (
    "preferred_name",
    "form_of_address",
    "pronouns",
    "primary_language",
    "primary_residence",
)
AUDIENCES = ("both", "private_only", "public_only")


def _empty_profile() -> dict[str, Any]:
    return {
        **{
            field: {audience: None for audience in AUDIENCES}
            for field in PROFILE_FIELDS
        },
    }


def _content_hash(content: str) -> str:
    normalized = re.sub(r"\s+", " ", content.strip()).casefold()
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def upgrade() -> None:
    connection = op.get_bind()
    source_rows = connection.execute(
        sa.text(
            "SELECT user_id, pin_kind, audience, content "
            "FROM memories WHERE pin_kind IS NOT NULL "
            "ORDER BY user_id, pin_kind, audience"
        )
    ).mappings().all()
    affected_users = {row["user_id"] for row in source_rows}
    if affected_users:
        existing_users = set(
            connection.execute(
                sa.text("SELECT user_id FROM settings WHERE key = 'user-profile'")
            ).scalars()
        ) & affected_users
        if existing_users:
            raise RuntimeError("user-profile settings already exist for migrated users")

    profiles: dict[Any, dict[str, Any]] = {}
    for row in source_rows:
        pin_kind = row["pin_kind"]
        audience = row["audience"]
        if pin_kind not in PROFILE_FIELDS or audience not in AUDIENCES:
            raise RuntimeError("invalid core identity memory encountered during migration")
        profile = profiles.setdefault(row["user_id"], _empty_profile())
        if profile[pin_kind][audience] is not None:
            raise RuntimeError("duplicate core identity memory encountered during migration")
        profile[pin_kind][audience] = row["content"]

    if profiles:
        settings = sa.table(
            "settings",
            sa.column("id", sa.Uuid()),
            sa.column("key", sa.String()),
            sa.column("user_id", sa.Uuid()),
            sa.column("data", postgresql.JSONB()),
        )
        connection.execute(
            sa.insert(settings),
            [
                {
                    "id": uuid4(),
                    "key": "user-profile",
                    "user_id": user_id,
                    "data": data,
                }
                for user_id, data in profiles.items()
            ],
        )

    op.execute(
        sa.text(
            """
            UPDATE approval_requests AS approval
            SET status = 'expired', resolved_at = COALESCE(resolved_at, now())
            WHERE request_type = 'memory_change'
              AND status = 'pending'
            """
        )
    )
    op.execute(sa.text("DELETE FROM memories WHERE pin_kind IS NOT NULL"))
    op.drop_index(
        op.f("uq_memories_user_pin_kind_audience"),
        table_name="memories",
        postgresql_where="(pin_kind IS NOT NULL)",
    )
    op.drop_constraint("pin_kind_identity_pinned", "memories", type_="check")
    op.drop_constraint("pin_kind_valid", "memories", type_="check")
    op.drop_column("memories", "pin_kind")


def downgrade() -> None:
    op.add_column(
        "memories",
        sa.Column("pin_kind", sa.VARCHAR(length=40), autoincrement=False, nullable=True),
    )
    connection = op.get_bind()
    profile_rows = connection.execute(
        sa.text("SELECT user_id, data FROM settings WHERE key = 'user-profile'")
    ).mappings().all()
    memories = sa.table(
        "memories",
        sa.column("id", sa.Uuid()),
        sa.column("user_id", sa.Uuid()),
        sa.column("source_type", sa.String()),
        sa.column("pinned", sa.Boolean()),
        sa.column("audience", sa.String()),
        sa.column("category", sa.String()),
        sa.column("revision", sa.Integer()),
        sa.column("content", sa.Text()),
        sa.column("normalized_content_hash", sa.String()),
        sa.column("pin_kind", sa.String()),
    )
    for profile_row in profile_rows:
        data = profile_row["data"] or {}
        for pin_kind in PROFILE_FIELDS:
            values = data.get(pin_kind) or {}
            for audience in AUDIENCES:
                content = values.get(audience)
                if not isinstance(content, str) or not content.strip():
                    continue
                content_hash = _content_hash(content)
                existing = connection.execute(
                    sa.text(
                        "SELECT id, pin_kind FROM memories "
                        "WHERE user_id = :user_id AND normalized_content_hash = :content_hash"
                    ),
                    {
                        "user_id": profile_row["user_id"],
                        "content_hash": content_hash,
                    },
                ).mappings().first()
                if existing is not None:
                    if existing["pin_kind"] is not None:
                        raise RuntimeError(
                            "duplicate profile content cannot be restored during downgrade"
                        )
                    connection.execute(
                        sa.update(memories)
                        .where(memories.c.id == existing["id"])
                        .values(
                            pinned=True,
                            audience=audience,
                            category="identity",
                            pin_kind=pin_kind,
                        )
                    )
                else:
                    connection.execute(
                        sa.insert(memories).values(
                            id=uuid4(),
                            user_id=profile_row["user_id"],
                            source_type="manual",
                            pinned=True,
                            audience=audience,
                            category="identity",
                            revision=1,
                            content=content.strip(),
                            normalized_content_hash=content_hash,
                            pin_kind=pin_kind,
                        )
                    )
    op.execute(sa.text("DELETE FROM settings WHERE key = 'user-profile'"))
    op.create_check_constraint(
        "pin_kind_valid",
        "memories",
        "pin_kind IS NULL OR pin_kind IN "
        "('preferred_name', 'form_of_address', 'pronouns', "
        "'primary_language', 'primary_residence')",
    )
    op.create_check_constraint(
        "pin_kind_identity_pinned",
        "memories",
        "pin_kind IS NULL OR (category = 'identity' AND pinned)",
    )
    op.create_index(
        op.f("uq_memories_user_pin_kind_audience"),
        "memories",
        ["user_id", "pin_kind", "audience"],
        unique=True,
        postgresql_where="(pin_kind IS NOT NULL)",
    )
