"""leave approvals basemodel indexes

Revision ID: 20260605_0015
Revises: 20260605_0014
Create Date: 2026-06-05 00:10:00.000000
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "20260605_0015"
down_revision: Union[str, None] = "20260605_0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # No-op: revision 20260605_0014 already creates leave_approvals with the
    # tenant_id column and the ix_leave_approvals_tenant_id / _deleted_at indexes.
    pass


def downgrade() -> None:
    # No-op: see upgrade().
    pass
