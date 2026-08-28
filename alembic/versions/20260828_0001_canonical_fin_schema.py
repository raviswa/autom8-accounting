"""canonical fin_* schema

Revision ID: 20260828_0001
Revises:
Create Date: 2026-08-28
"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "20260828_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "fin_parties",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("source_ref", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("gstin", sa.String(length=32), nullable=True),
        sa.Column(
            "external_mappings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_system",
            "source_ref",
            name="uq_fin_parties_tenant_source",
        ),
    )
    op.create_index("ix_fin_parties_tenant_id", "fin_parties", ["tenant_id"])

    op.create_table(
        "fin_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("source_ref", sa.String(length=255), nullable=False),
        sa.Column("name", sa.String(length=512), nullable=False),
        sa.Column("hsn_sac", sa.String(length=32), nullable=True),
        sa.Column("tax_rate", sa.Numeric(8, 4), nullable=True),
        sa.Column(
            "external_mappings",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_system",
            "source_ref",
            name="uq_fin_items_tenant_source",
        ),
    )
    op.create_index("ix_fin_items_tenant_id", "fin_items", ["tenant_id"])

    op.create_table(
        "fin_transactions",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_system", sa.String(length=64), nullable=False),
        sa.Column("source_ref", sa.String(length=255), nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("type", sa.String(length=32), nullable=False),
        sa.Column("category", sa.String(length=128), nullable=True),
        sa.Column("party_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("tax_amount", sa.Numeric(14, 2), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "tax_breakdown",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("payment_mode", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["party_id"], ["fin_parties.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "tenant_id",
            "source_system",
            "source_ref",
            name="uq_fin_transactions_tenant_source",
        ),
        sa.CheckConstraint(
            "type IN ('sale','purchase','payment','refund','stock_adjustment')",
            name="ck_fin_transactions_type",
        ),
    )
    op.create_index("ix_fin_transactions_tenant_id", "fin_transactions", ["tenant_id"])
    op.create_index(
        "ix_fin_transactions_tenant_date", "fin_transactions", ["tenant_id", "date"]
    )

    op.create_table(
        "fin_transaction_lines",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transaction_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("item_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("qty", sa.Numeric(14, 4), nullable=False),
        sa.Column("rate", sa.Numeric(14, 4), nullable=False),
        sa.Column("hsn_sac", sa.String(length=32), nullable=True),
        sa.Column("line_amount", sa.Numeric(14, 2), nullable=False),
        sa.Column("line_tax", sa.Numeric(14, 2), server_default=sa.text("0"), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["fin_items.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["fin_transactions.id"], ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_fin_transaction_lines_tenant_id", "fin_transaction_lines", ["tenant_id"]
    )
    op.create_index(
        "ix_fin_transaction_lines_transaction_id",
        "fin_transaction_lines",
        ["transaction_id"],
    )

    op.create_table(
        "fin_sync_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tenant_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("transaction_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("system", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("error_detail", sa.Text(), nullable=True),
        sa.Column(
            "attempted_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "direction IN ('inbound','outbound')",
            name="ck_fin_sync_log_direction",
        ),
        sa.ForeignKeyConstraint(
            ["transaction_id"], ["fin_transactions.id"], ondelete="SET NULL"
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_fin_sync_log_tenant_id", "fin_sync_log", ["tenant_id"])
    op.create_index(
        "ix_fin_sync_log_tx_attempted",
        "fin_sync_log",
        ["transaction_id", "attempted_at"],
    )


def downgrade() -> None:
    op.drop_table("fin_sync_log")
    op.drop_table("fin_transaction_lines")
    op.drop_table("fin_transactions")
    op.drop_table("fin_items")
    op.drop_table("fin_parties")
