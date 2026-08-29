"""Add fin_transactions.occurred_at for hourly reports

Revision ID: 20260830_0003
Revises: 20260829_0002
Create Date: 2026-08-30
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260830_0003"
down_revision: Union[str, None] = "20260829_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "fin_transactions",
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index(
        "ix_fin_transactions_tenant_occurred_at",
        "fin_transactions",
        ["tenant_id", "occurred_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_fin_transactions_tenant_occurred_at", table_name="fin_transactions")
    op.drop_column("fin_transactions", "occurred_at")
