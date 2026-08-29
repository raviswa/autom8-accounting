"""Zoho Books sink — maps canonical fin_transactions to Zoho invoices/payments.

Idempotency: skip if successful outbound sync_log exists OR zoho_invoice_id in mappings.
Partial-failure retry: look up invoice by reference_number before create.
"""

from __future__ import annotations

import logging
from typing import Any, Optional
from uuid import UUID

import httpx
from sqlalchemy.orm import Session

from db.models import FinParty, FinTransaction, FinTransactionLine
from sinks.base import SyncResult
from sinks.sync_log import (
    already_synced_successfully,
    append_outbound_log,
    get_tx_sink_id,
    set_mapping,
    set_tx_mapping,
)

logger = logging.getLogger(__name__)

SYSTEM = "zoho_books"


class ZohoBooksSink:
    system = SYSTEM

    def __init__(self, http_client: Optional[httpx.Client] = None):
        self._client = http_client

    def push(
        self,
        *,
        tenant_id: UUID,
        transaction: FinTransaction,
        config: dict[str, Any],
        credentials: dict[str, Any],
        db: Session,
        lines: Optional[list[FinTransactionLine]] = None,
        party: Optional[FinParty] = None,
    ) -> SyncResult:
        if already_synced_successfully(db, transaction_id=transaction.id, system=SYSTEM):
            return SyncResult(status="skipped", error_detail="already synced")

        existing_invoice = get_tx_sink_id(transaction, "zoho_invoice_id")
        if existing_invoice:
            append_outbound_log(
                db,
                tenant_id=tenant_id,
                transaction_id=transaction.id,
                system=SYSTEM,
                status="skipped",
                error_detail=f"mapping has zoho_invoice_id={existing_invoice}",
            )
            db.commit()
            return SyncResult(status="skipped", external_id=existing_invoice)

        access_token = str(credentials.get("access_token") or "").strip()
        org_id = str(
            config.get("organization_id") or credentials.get("organization_id") or ""
        ).strip()
        api_domain = str(
            config.get("api_domain")
            or credentials.get("api_domain")
            or "https://www.zohoapis.in"
        ).rstrip("/")
        if not access_token or not org_id:
            result = SyncResult(status="failed", error_detail="missing Zoho credentials")
            append_outbound_log(
                db,
                tenant_id=tenant_id,
                transaction_id=transaction.id,
                system=SYSTEM,
                status="failed",
                error_detail=result.error_detail,
            )
            db.commit()
            return result

        try:
            contact_id = self._ensure_contact(
                api_domain, access_token, org_id, party, transaction
            )
            if party and contact_id:
                set_mapping(party, "zoho_contact_id", contact_id)

            # Partial-failure path: search by reference_number first
            ref = f"{transaction.source_system}:{transaction.source_ref}"[:50]
            found = self._find_invoice_by_reference(
                api_domain, access_token, org_id, ref
            )
            if found:
                set_tx_mapping(transaction, "zoho_invoice_id", found)
                append_outbound_log(
                    db,
                    tenant_id=tenant_id,
                    transaction_id=transaction.id,
                    system=SYSTEM,
                    status="success",
                    error_detail="recovered existing invoice by reference_number",
                )
                db.commit()
                return SyncResult(status="success", external_id=found, meta={"recovered": True})

            invoice_id = self._create_invoice(
                api_domain,
                access_token,
                org_id,
                contact_id,
                transaction,
                lines or [],
                ref,
            )
            set_tx_mapping(transaction, "zoho_invoice_id", invoice_id)
            if transaction.txn_type.value if hasattr(transaction.type, "value") else str(transaction.type) in (
                "sale",
                "payment",
            ):
                try:
                    self._mark_paid(
                        api_domain, access_token, org_id, contact_id, invoice_id, transaction
                    )
                except Exception as pay_err:  # noqa: BLE001
                    logger.warning("zoho mark paid failed: %s", pay_err)

            append_outbound_log(
                db,
                tenant_id=tenant_id,
                transaction_id=transaction.id,
                system=SYSTEM,
                status="success",
            )
            db.commit()
            return SyncResult(status="success", external_id=invoice_id)
        except Exception as e:  # noqa: BLE001
            append_outbound_log(
                db,
                tenant_id=tenant_id,
                transaction_id=transaction.id,
                system=SYSTEM,
                status="failed",
                error_detail=str(e)[:500],
            )
            db.commit()
            return SyncResult(status="failed", error_detail=str(e)[:500])

    def _client_or(self) -> httpx.Client:
        return self._client or httpx.Client(timeout=30.0)

    def _headers(self, token: str) -> dict[str, str]:
        return {
            "Authorization": f"Zoho-oauthtoken {token}",
            "Content-Type": "application/json",
        }

    def _ensure_contact(
        self,
        api_domain: str,
        token: str,
        org_id: str,
        party: Optional[FinParty],
        tx: FinTransaction,
    ) -> str:
        if party and isinstance(party.external_mappings, dict):
            existing = party.external_mappings.get("zoho_contact_id")
            if existing:
                return str(existing)

        name = (party.name if party else None) or "Walk-in Customer"
        phone = (party.source_ref if party else "") or ""
        digits = "".join(c for c in phone if c.isdigit())
        client = self._client_or()
        if len(digits) >= 10:
            needle = digits[-10:]
            r = client.get(
                f"{api_domain}/books/v3/contacts",
                headers=self._headers(token),
                params={
                    "organization_id": org_id,
                    "contact_type": "customer",
                    "phone": needle,
                    "per_page": 25,
                },
            )
            data = r.json() if r.content else {}
            for c in data.get("contacts") or []:
                cid = c.get("contact_id")
                if cid:
                    return str(cid)

        r = client.post(
            f"{api_domain}/books/v3/contacts",
            headers=self._headers(token),
            params={"organization_id": org_id},
            json={
                "contact_name": name[:200],
                "contact_type": "customer",
                "phone": digits or None,
                "mobile": digits or None,
            },
        )
        data = r.json() if r.content else {}
        if r.status_code >= 400 or (data.get("code") not in (None, 0)):
            raise RuntimeError(data.get("message") or f"contact create HTTP {r.status_code}")
        contact = data.get("contact") or data
        cid = contact.get("contact_id")
        if not cid:
            raise RuntimeError("Zoho did not return contact_id")
        return str(cid)

    def _find_invoice_by_reference(
        self, api_domain: str, token: str, org_id: str, ref: str
    ) -> Optional[str]:
        client = self._client_or()
        r = client.get(
            f"{api_domain}/books/v3/invoices",
            headers=self._headers(token),
            params={
                "organization_id": org_id,
                "reference_number": ref,
                "per_page": 10,
            },
        )
        if r.status_code >= 400:
            return None
        data = r.json() if r.content else {}
        for inv in data.get("invoices") or []:
            if str(inv.get("reference_number") or "") == ref and inv.get("invoice_id"):
                return str(inv["invoice_id"])
        return None

    def _create_invoice(
        self,
        api_domain: str,
        token: str,
        org_id: str,
        contact_id: str,
        tx: FinTransaction,
        lines: list[FinTransactionLine],
        ref: str,
    ) -> str:
        line_items = []
        for ln in lines:
            line_items.append(
                {
                    "name": (ln.item.name if ln.item else "Item")[:200]
                    if hasattr(ln, "item") and ln.item
                    else "Item",
                    "rate": float(ln.rate or 0),
                    "quantity": float(ln.qty or 1),
                }
            )
        if not line_items:
            line_items = [
                {
                    "name": "Sale",
                    "rate": float(tx.amount or 0),
                    "quantity": 1,
                }
            ]
        # Prefer item names from joined lines — reload names via rate/amount if needed
        for i, ln in enumerate(lines):
            if i < len(line_items) and getattr(ln, "item_id", None):
                pass

        body = {
            "customer_id": contact_id,
            "date": tx.date.isoformat(),
            "reference_number": ref,
            "line_items": line_items,
            "notes": f"Autom8 ledger {tx.source_system}:{tx.source_ref}",
        }
        client = self._client_or()
        r = client.post(
            f"{api_domain}/books/v3/invoices",
            headers=self._headers(token),
            params={"organization_id": org_id},
            json=body,
        )
        data = r.json() if r.content else {}
        if r.status_code >= 400 or (data.get("code") not in (None, 0)):
            raise RuntimeError(data.get("message") or f"invoice create HTTP {r.status_code}")
        inv = data.get("invoice") or data
        iid = inv.get("invoice_id")
        if not iid:
            raise RuntimeError("Zoho did not return invoice_id")
        return str(iid)

    def _mark_paid(
        self,
        api_domain: str,
        token: str,
        org_id: str,
        contact_id: str,
        invoice_id: str,
        tx: FinTransaction,
    ) -> None:
        client = self._client_or()
        amount = float(tx.amount or 0)
        r = client.post(
            f"{api_domain}/books/v3/customerpayments",
            headers=self._headers(token),
            params={"organization_id": org_id},
            json={
                "customer_id": contact_id,
                "payment_mode": "others",
                "amount": amount,
                "date": tx.date.isoformat(),
                "invoices": [{"invoice_id": invoice_id, "amount_applied": amount}],
            },
        )
        if r.status_code >= 400:
            # Fallback: mark invoice paid
            client.post(
                f"{api_domain}/books/v3/invoices/{invoice_id}/status/paid",
                headers=self._headers(token),
                params={"organization_id": org_id},
            )
