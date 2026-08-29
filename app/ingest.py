"""Persist NormalizedTransaction with UNIQUE + reconcile guards."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from db.models import (
    FinItem,
    FinParty,
    FinSyncLog,
    FinTransaction,
    FinTransactionLine,
    TransactionType,
)
from db.reconcile import reconcile_header_vs_lines
from sources.base import NormalizedTransaction


class ReconcileError(ValueError):
    """Rule 3 failed — header does not match line totals."""


@dataclass
class IngestResult:
    transaction_id: Optional[uuid.UUID]
    created: bool
    duplicate: bool
    error: Optional[str] = None


def _upsert_party(db: Session, nt: NormalizedTransaction) -> Optional[uuid.UUID]:
    if not nt.party:
        return None
    existing = (
        db.query(FinParty)
        .filter_by(
            tenant_id=nt.tenant_id,
            source_system=nt.source_system,
            source_ref=nt.party.source_ref,
        )
        .one_or_none()
    )
    if existing:
        existing.name = nt.party.name
        if nt.party.gstin:
            existing.gstin = nt.party.gstin
        return existing.id
    party = FinParty(
        id=uuid.uuid4(),
        tenant_id=nt.tenant_id,
        source_system=nt.source_system,
        source_ref=nt.party.source_ref,
        name=nt.party.name,
        gstin=nt.party.gstin,
        external_mappings={},
    )
    db.add(party)
    db.flush()
    return party.id


def _upsert_item(
    db: Session,
    nt: NormalizedTransaction,
    item_source_ref: str,
    name: str,
    hsn_sac: Optional[str],
    tax_rate: Optional[Decimal],
) -> uuid.UUID:
    existing = (
        db.query(FinItem)
        .filter_by(
            tenant_id=nt.tenant_id,
            source_system=nt.source_system,
            source_ref=item_source_ref,
        )
        .one_or_none()
    )
    if existing:
        existing.name = name
        if hsn_sac:
            existing.hsn_sac = hsn_sac
        if tax_rate is not None:
            existing.tax_rate = tax_rate
        return existing.id
    item = FinItem(
        id=uuid.uuid4(),
        tenant_id=nt.tenant_id,
        source_system=nt.source_system,
        source_ref=item_source_ref,
        name=name,
        hsn_sac=hsn_sac,
        tax_rate=tax_rate,
        external_mappings={},
    )
    db.add(item)
    db.flush()
    return item.id


def _append_sync_log(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    transaction_id: Optional[uuid.UUID],
    status: str,
    error_detail: Optional[str] = None,
    system: str = "autom8",
) -> None:
    db.add(
        FinSyncLog(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            transaction_id=transaction_id,
            direction="inbound",
            system=system,
            status=status,
            error_detail=error_detail,
        )
    )


def ingest_normalized(db: Session, nt: NormalizedTransaction) -> IngestResult:
    """
    Transactional insert. Relies on DB UNIQUE for concurrency (rules 1 + 4).
    Enforces line/header reconcile before insert (rule 3).
    """
    # Existing row → idempotent success (no second insert)
    existing = (
        db.query(FinTransaction)
        .filter_by(
            tenant_id=nt.tenant_id,
            source_system=nt.source_system,
            source_ref=nt.source_ref,
        )
        .one_or_none()
    )
    if existing:
        # Refresh sale timestamp on re-ingest (backfill) so heatmaps can recover hours
        if nt.occurred_at and (
            existing.occurred_at is None or existing.occurred_at != nt.occurred_at
        ):
            existing.occurred_at = nt.occurred_at
        _append_sync_log(
            db,
            tenant_id=nt.tenant_id,
            transaction_id=existing.id,
            status="skipped",
            error_detail="duplicate source_ref",
        )
        db.commit()
        return IngestResult(
            transaction_id=existing.id, created=False, duplicate=True
        )

    rec = reconcile_header_vs_lines(
        nt.amount,
        nt.tax_amount,
        [ln.line_amount for ln in nt.lines],
        [ln.line_tax for ln in nt.lines],
    )
    # Allow zero-line monetary txs only when amount and tax are zero (edge cases)
    if nt.lines:
        if not rec.ok:
            msg = (
                f"line reconcile failed: header={rec.header_total} "
                f"lines={rec.lines_total} delta={rec.delta}"
            )
            _append_sync_log(
                db,
                tenant_id=nt.tenant_id,
                transaction_id=None,
                status="failed",
                error_detail=msg,
            )
            db.commit()
            raise ReconcileError(msg)
    elif nt.amount != 0 or nt.tax_amount != 0:
        msg = "transaction has amount/tax but no lines"
        _append_sync_log(
            db,
            tenant_id=nt.tenant_id,
            transaction_id=None,
            status="failed",
            error_detail=msg,
        )
        db.commit()
        raise ReconcileError(msg)

    try:
        party_id = _upsert_party(db, nt)
        tx = FinTransaction(
            id=uuid.uuid4(),
            tenant_id=nt.tenant_id,
            source_system=nt.source_system,
            source_ref=nt.source_ref,
            date=nt.txn_date,
            occurred_at=nt.occurred_at,
            type=TransactionType(nt.txn_type),
            category=nt.category,
            party_id=party_id,
            amount=nt.amount,
            tax_amount=nt.tax_amount,
            tax_breakdown=nt.tax_breakdown or {},
            payment_mode=nt.payment_mode,
        )
        db.add(tx)
        db.flush()

        for ln in nt.lines:
            item_id = _upsert_item(
                db,
                nt,
                ln.item_source_ref,
                ln.item_name,
                ln.hsn_sac,
                ln.tax_rate,
            )
            db.add(
                FinTransactionLine(
                    id=uuid.uuid4(),
                    tenant_id=nt.tenant_id,
                    transaction_id=tx.id,
                    item_id=item_id,
                    qty=ln.qty,
                    rate=ln.rate,
                    hsn_sac=ln.hsn_sac,
                    line_amount=ln.line_amount,
                    line_tax=ln.line_tax,
                )
            )

        _append_sync_log(
            db,
            tenant_id=nt.tenant_id,
            transaction_id=tx.id,
            status="success",
        )
        db.commit()
        return IngestResult(transaction_id=tx.id, created=True, duplicate=False)
    except IntegrityError:
        db.rollback()
        # Concurrent insert won the race — treat as duplicate
        again = (
            db.query(FinTransaction)
            .filter_by(
                tenant_id=nt.tenant_id,
                source_system=nt.source_system,
                source_ref=nt.source_ref,
            )
            .one_or_none()
        )
        if again:
            _append_sync_log(
                db,
                tenant_id=nt.tenant_id,
                transaction_id=again.id,
                status="skipped",
                error_detail="concurrent duplicate",
            )
            db.commit()
            return IngestResult(
                transaction_id=again.id, created=False, duplicate=True
            )
        raise


def ingest_raw_autom8(db: Session, raw: dict[str, Any]) -> IngestResult:
    from sources.autom8 import translate

    nt = translate(raw)
    return ingest_normalized(db, nt)
