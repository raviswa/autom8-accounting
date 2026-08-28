from __future__ import annotations

import enum
import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship
from sqlalchemy.types import JSON, TypeDecorator


class GUID(TypeDecorator):
    """Platform-independent UUID type."""

    impl = String(36)
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(UUID(as_uuid=True))
        return dialect.type_descriptor(String(36))

    def process_bind_param(self, value, dialect):
        if value is None:
            return value
        if isinstance(value, uuid.UUID):
            return value if dialect.name == "postgresql" else str(value)
        return uuid.UUID(str(value)) if dialect.name == "postgresql" else str(value)

    def process_result_value(self, value, dialect):
        if value is None:
            return value
        return value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))


class JSONType(TypeDecorator):
    impl = JSON
    cache_ok = True

    def load_dialect_impl(self, dialect):
        if dialect.name == "postgresql":
            return dialect.type_descriptor(JSONB())
        return dialect.type_descriptor(JSON())


class Base(DeclarativeBase):
    pass


class TransactionType(str, enum.Enum):
    sale = "sale"
    purchase = "purchase"
    payment = "payment"
    refund = "refund"
    stock_adjustment = "stock_adjustment"


class SyncDirection(str, enum.Enum):
    inbound = "inbound"
    outbound = "outbound"


class SyncStatus(str, enum.Enum):
    pending = "pending"
    success = "success"
    failed = "failed"
    skipped = "skipped"


class FinParty(Base):
    __tablename__ = "fin_parties"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "source_system", "source_ref", name="uq_fin_parties_tenant_source"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    gstin: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    external_mappings: Mapped[dict[str, Any]] = mapped_column(
        JSONType(), nullable=False, server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FinItem(Base):
    __tablename__ = "fin_items"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id", "source_system", "source_ref", name="uq_fin_items_tenant_source"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    hsn_sac: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    tax_rate: Mapped[Optional[Decimal]] = mapped_column(Numeric(8, 4), nullable=True)
    external_mappings: Mapped[dict[str, Any]] = mapped_column(
        JSONType(), nullable=False, server_default=text("'{}'")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class FinTransaction(Base):
    __tablename__ = "fin_transactions"
    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "source_system",
            "source_ref",
            name="uq_fin_transactions_tenant_source",
        ),
        Index("ix_fin_transactions_tenant_date", "tenant_id", "date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    source_ref: Mapped[str] = mapped_column(String(255), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    type: Mapped[TransactionType] = mapped_column(
        Enum(TransactionType, name="fin_transaction_type", native_enum=False),
        nullable=False,
    )
    category: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    party_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(), ForeignKey("fin_parties.id", ondelete="SET NULL"), nullable=True
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    tax_amount: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0")
    )
    tax_breakdown: Mapped[dict[str, Any]] = mapped_column(
        JSONType(), nullable=False, server_default=text("'{}'")
    )
    payment_mode: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )

    lines: Mapped[list[FinTransactionLine]] = relationship(
        "FinTransactionLine",
        back_populates="transaction",
        cascade="all, delete-orphan",
    )
    party: Mapped[Optional[FinParty]] = relationship("FinParty")


class FinTransactionLine(Base):
    __tablename__ = "fin_transaction_lines"

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        GUID(), ForeignKey("fin_transactions.id", ondelete="CASCADE"), nullable=False, index=True
    )
    item_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(), ForeignKey("fin_items.id", ondelete="SET NULL"), nullable=True
    )
    qty: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    rate: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    hsn_sac: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    line_amount: Mapped[Decimal] = mapped_column(Numeric(14, 2), nullable=False)
    line_tax: Mapped[Decimal] = mapped_column(
        Numeric(14, 2), nullable=False, server_default=text("0")
    )

    transaction: Mapped[FinTransaction] = relationship("FinTransaction", back_populates="lines")
    item: Mapped[Optional[FinItem]] = relationship("FinItem")


class FinSyncLog(Base):
    """Append-only audit of sync attempts. Never UPDATE status in place — insert a new row."""

    __tablename__ = "fin_sync_log"
    __table_args__ = (
        CheckConstraint(
            "direction IN ('inbound', 'outbound')",
            name="ck_fin_sync_log_direction",
        ),
        Index("ix_fin_sync_log_tx_attempted", "transaction_id", "attempted_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(GUID(), primary_key=True, default=uuid.uuid4)
    tenant_id: Mapped[uuid.UUID] = mapped_column(GUID(), nullable=False, index=True)
    transaction_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        GUID(), ForeignKey("fin_transactions.id", ondelete="SET NULL"), nullable=True
    )
    direction: Mapped[str] = mapped_column(String(16), nullable=False)
    system: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    error_detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    attempted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
