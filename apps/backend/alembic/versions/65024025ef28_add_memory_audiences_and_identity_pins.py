"""add memory audiences and identity pins

Revision ID: 65024025ef28
Revises: 83588852dbeb
Create Date: 2026-08-20 21:44:08.184210
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '65024025ef28'
down_revision: Union[str, None] = '83588852dbeb'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "memories", sa.Column("audience", sa.String(length=20), nullable=True)
    )
    op.add_column(
        "memories", sa.Column("pin_kind", sa.String(length=40), nullable=True)
    )
    op.execute(
        sa.text(
            "UPDATE memories SET audience = CASE trust_level "
            "WHEN 'private' THEN 'private_only' ELSE 'both' END"
        )
    )
    op.alter_column("memories", "audience", nullable=False)
    op.execute(
        sa.text(
            """
            UPDATE approval_requests AS approvals
            SET payload = jsonb_set(
                jsonb_set(
                    approvals.payload,
                    '{targetMemories}',
                    (
                        SELECT jsonb_agg(
                            (target - 'trustLevel'::text) || jsonb_build_object(
                                'audience',
                                CASE target->>'trustLevel'
                                    WHEN 'private' THEN 'private_only'
                                    ELSE 'both'
                                END
                            )
                            ORDER BY ordinal
                        )
                        FROM jsonb_array_elements(
                            approvals.payload->'targetMemories'
                        ) WITH ORDINALITY AS targets(target, ordinal)
                    )
                ),
                '{after}',
                CASE
                    WHEN jsonb_typeof(approvals.payload->'after') = 'object'
                    THEN (
                        ((approvals.payload->'after') - 'trustLevel'::text)
                        || jsonb_build_object(
                            'audience',
                            CASE approvals.payload->'after'->>'trustLevel'
                                WHEN 'private' THEN 'private_only'
                                ELSE 'both'
                            END
                        )
                        || CASE
                            WHEN approvals.payload ? 'pinKind'
                            THEN jsonb_build_object(
                                'pinKind', approvals.payload->'pinKind'
                            )
                            ELSE '{}'::jsonb
                        END
                    )
                    ELSE COALESCE(approvals.payload->'after', 'null'::jsonb)
                END
            )
            WHERE approvals.request_type = 'memory_change'
              AND approvals.status = 'pending'
              AND jsonb_typeof(approvals.payload->'targetMemories') = 'array'
              AND EXISTS (
                  SELECT 1
                  FROM jsonb_array_elements(
                      approvals.payload->'targetMemories'
                  ) AS target
                  WHERE target ? 'trustLevel'
              )
            """
        )
    )
    op.drop_constraint("trust_level_valid", "memories", type_="check")
    op.create_check_constraint(
        "audience_valid",
        "memories",
        "audience IN ('private_only', 'public_only', 'both')",
    )
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
        "uq_memories_user_pin_kind_audience",
        "memories",
        ["user_id", "pin_kind", "audience"],
        unique=True,
        postgresql_where=sa.text("pin_kind IS NOT NULL"),
    )
    op.drop_column("memories", "trust_level")


def downgrade() -> None:
    op.add_column(
        "memories",
        sa.Column(
            "trust_level",
            sa.VARCHAR(length=20),
            autoincrement=False,
            nullable=True,
        ),
    )
    op.execute(
        sa.text(
            "UPDATE memories SET trust_level = CASE audience "
            "WHEN 'private_only' THEN 'private' ELSE 'public' END"
        )
    )
    op.alter_column("memories", "trust_level", nullable=False)
    op.execute(
        sa.text(
            """
            UPDATE approval_requests AS approvals
            SET payload = jsonb_set(
                jsonb_set(
                    approvals.payload,
                    '{targetMemories}',
                    (
                        SELECT jsonb_agg(
                            (target - 'audience'::text) || jsonb_build_object(
                                'trustLevel',
                                CASE target->>'audience'
                                    WHEN 'private_only' THEN 'private'
                                    ELSE 'public'
                                END
                            )
                            ORDER BY ordinal
                        )
                        FROM jsonb_array_elements(
                            approvals.payload->'targetMemories'
                        ) WITH ORDINALITY AS targets(target, ordinal)
                    )
                ),
                '{after}',
                CASE
                    WHEN jsonb_typeof(approvals.payload->'after') = 'object'
                    THEN (
                        ((approvals.payload->'after') - 'audience'::text)
                        - 'pinKind'::text
                    )
                        || jsonb_build_object(
                            'trustLevel',
                            CASE approvals.payload->'after'->>'audience'
                                WHEN 'private_only' THEN 'private'
                                ELSE 'public'
                            END
                        )
                    ELSE COALESCE(approvals.payload->'after', 'null'::jsonb)
                END
            )
            WHERE approvals.request_type = 'memory_change'
              AND approvals.status = 'pending'
              AND jsonb_typeof(approvals.payload->'targetMemories') = 'array'
              AND EXISTS (
                  SELECT 1
                  FROM jsonb_array_elements(
                      approvals.payload->'targetMemories'
                  ) AS target
                  WHERE target ? 'audience'
              )
            """
        )
    )
    op.create_check_constraint(
        "trust_level_valid",
        "memories",
        "trust_level IN ('public', 'private')",
    )
    op.drop_index("uq_memories_user_pin_kind_audience", table_name="memories")
    op.drop_constraint("pin_kind_identity_pinned", "memories", type_="check")
    op.drop_constraint("pin_kind_valid", "memories", type_="check")
    op.drop_constraint("audience_valid", "memories", type_="check")
    op.drop_column("memories", "pin_kind")
    op.drop_column("memories", "audience")
