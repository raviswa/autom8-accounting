"""Dispatch outbound sinks for a transaction (append-only sync_log)."""

from __future__ import annotations

import logging
import uuid
from typing import Any

from sqlalchemy.orm import Session, joinedload

from db.models import FinSinkConfig, FinTransaction, FinTransactionLine
from sinks.tally import TallySink
from sinks.zoho_books import ZohoBooksSink

logger = logging.getLogger(__name__)


def load_active_sinks(db: Session, tenant_id: uuid.UUID) -> list[FinSinkConfig]:
    return (
        db.query(FinSinkConfig)
        .filter_by(tenant_id=tenant_id, is_active=True)
        .all()
    )


def push_transaction_to_sinks(db: Session, transaction_id: uuid.UUID) -> list[dict[str, Any]]:
    tx = (
        db.query(FinTransaction)
        .options(joinedload(FinTransaction.lines).joinedload(FinTransactionLine.item))
        .options(joinedload(FinTransaction.party))
        .filter_by(id=transaction_id)
        .one_or_none()
    )
    if not tx:
        return [{"ok": False, "error": "transaction not found"}]

    results = []
    for cfg in load_active_sinks(db, tx.tenant_id):
        try:
            if cfg.system == "zoho_books":
                sink = ZohoBooksSink()
                res = sink.push(
                    tenant_id=tx.tenant_id,
                    transaction=tx,
                    config=cfg.config or {},
                    credentials=cfg.credentials or {},
                    db=db,
                    lines=list(tx.lines or []),
                    party=tx.party,
                )
            elif cfg.system == "tally":
                sink = TallySink()
                res = sink.push(
                    tenant_id=tx.tenant_id,
                    transaction=tx,
                    config=cfg.config or {},
                    credentials=cfg.credentials or {},
                    db=db,
                    lines=list(tx.lines or []),
                    party=tx.party,
                )
            else:
                continue
            results.append(
                {
                    "system": cfg.system,
                    "status": res.status,
                    "external_id": res.external_id,
                    "error_detail": res.error_detail,
                }
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("sink %s failed", cfg.system)
            results.append({"system": cfg.system, "status": "failed", "error_detail": str(e)})
    return results
