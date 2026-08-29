"""Append-only outbound sync helpers + idempotency lookups."""

from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy.orm import Session

from db.models import FinParty, FinSyncLog, FinTransaction


def already_synced_successfully(
    db: Session, *, transaction_id: uuid.UUID, system: str
) -> bool:
    row = (
        db.query(FinSyncLog)
        .filter_by(
            transaction_id=transaction_id,
            direction="outbound",
            system=system,
            status="success",
        )
        .order_by(FinSyncLog.attempted_at.desc())
        .first()
    )
    return row is not None


def external_id_from_mappings(party: Optional[FinParty], system: str) -> Optional[str]:
    if not party or not isinstance(party.external_mappings, dict):
        return None
    key = {
        "zoho_books": "zoho_contact_id",
        "tally": "tally_ledger_name",
    }.get(system)
    if not key:
        return None
    val = party.external_mappings.get(key)
    return str(val) if val else None


def set_mapping(party: FinParty, key: str, value: str) -> None:
    maps = dict(party.external_mappings or {})
    maps[key] = value
    party.external_mappings = maps


def set_tx_mapping(tx: FinTransaction, key: str, value: str) -> None:
    # Store sink IDs on party when possible; also stash on tax_breakdown._sinks for header refs
    breakdown = dict(tx.tax_breakdown or {})
    sinks = dict(breakdown.get("_sinks") or {})
    sinks[key] = value
    breakdown["_sinks"] = sinks
    tx.tax_breakdown = breakdown


def get_tx_sink_id(tx: FinTransaction, key: str) -> Optional[str]:
    breakdown = tx.tax_breakdown or {}
    sinks = breakdown.get("_sinks") if isinstance(breakdown, dict) else None
    if isinstance(sinks, dict) and sinks.get(key):
        return str(sinks[key])
    return None


def append_outbound_log(
    db: Session,
    *,
    tenant_id: uuid.UUID,
    transaction_id: uuid.UUID,
    system: str,
    status: str,
    error_detail: Optional[str] = None,
) -> None:
    db.add(
        FinSyncLog(
            id=uuid.uuid4(),
            tenant_id=tenant_id,
            transaction_id=transaction_id,
            direction="outbound",
            system=system,
            status=status,
            error_detail=error_detail,
        )
    )
