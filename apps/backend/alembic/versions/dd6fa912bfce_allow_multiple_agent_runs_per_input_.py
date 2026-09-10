"""allow multiple agent runs per input message

Revision ID: dd6fa912bfce
Revises: abc51989dc43
Create Date: 2026-09-10 08:22:22.408016
"""

from typing import Sequence, Union

from alembic import op


revision: str = 'dd6fa912bfce'
down_revision: Union[str, None] = 'abc51989dc43'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(op.f('uq_agent_runs_input_message_id'), 'agent_runs', type_='unique')
    op.create_index(op.f('ix_agent_runs_input_message_id'), 'agent_runs', ['input_message_id'], unique=False)


def downgrade() -> None:
    # Existing retries make this fail transactionally rather than discard run history.
    op.drop_index(op.f('ix_agent_runs_input_message_id'), table_name='agent_runs')
    op.create_unique_constraint(op.f('uq_agent_runs_input_message_id'), 'agent_runs', ['input_message_id'], postgresql_nulls_not_distinct=False)
