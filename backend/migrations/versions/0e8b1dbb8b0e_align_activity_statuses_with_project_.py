"""align activity statuses with project contract

Revision ID: 0e8b1dbb8b0e
Revises: 47f8534a1543
Create Date: 2026-10-27 22:16:06.352154

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '0e8b1dbb8b0e'
down_revision: Union[str, Sequence[str], None] = '47f8534a1543'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        "ck_activity_status",
        "planned_activities",
        type_="check",
    )
    op.create_check_constraint(
        "ck_activity_status",
        "planned_activities",
        "status IN ('not_started', 'in_progress', 'completed', "
        "'blocked', 'on_hold', 'unknown')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_activity_status",
        "planned_activities",
        type_="check",
    )
    op.create_check_constraint(
        "ck_activity_status",
        "planned_activities",
        "status IN ('not_started', 'in_progress', 'completed', "
        "'on_hold', 'cancelled', 'unknown')",
    )