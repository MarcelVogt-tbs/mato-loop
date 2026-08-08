"""allow rejected agent questions

Revision ID: 78b3c34601d8
Revises: 24e320c70600
Create Date: 2026-08-07 13:44:11.465624
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "78b3c34601d8"
down_revision: Union[str, None] = "24e320c70600"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Alembic does not detect changed check expressions during autogeneration.
    op.drop_constraint(
        op.f("ck_question_requests_status_valid"),
        "question_requests",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_question_requests_status_valid"),
        "question_requests",
        "status IN ('pending', 'answered', 'rejected', 'expired')",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_question_requests_status_valid"),
        "question_requests",
        type_="check",
    )
    op.execute(
        sa.text(
            "UPDATE question_requests SET status = 'expired' WHERE status = 'rejected'"
        )
    )
    op.create_check_constraint(
        op.f("ck_question_requests_status_valid"),
        "question_requests",
        "status IN ('pending', 'answered', 'expired')",
    )
