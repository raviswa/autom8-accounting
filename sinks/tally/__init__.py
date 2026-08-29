"""Tally sink — dual delivery modes (owner-selectable):

- xml_http: POST Tally XML voucher to a local/gateway HTTP URL (TallyPrime XML server)
- file_export: write the same XML to an export directory for manual/scheduled import

Config keys (fin_sink_configs.config):
  delivery_mode: "xml_http" | "file_export"
  gateway_url: required for xml_http (e.g. http://127.0.0.1:9000)
  export_dir: required for file_export (absolute path on the accounting host / sidecar)
  company_name: optional Tally company
  sales_ledger: default "Sales"
  party_ledger_prefix: optional
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any, Optional
from uuid import UUID
from xml.sax.saxutils import escape

import httpx
from sqlalchemy.orm import Session

from db.models import FinParty, FinTransaction, FinTransactionLine
from sinks.base import SyncResult
from sinks.sync_log import (
    already_synced_successfully,
    append_outbound_log,
    get_tx_sink_id,
    set_tx_mapping,
)

logger = logging.getLogger(__name__)

SYSTEM = "tally"
VALID_MODES = frozenset({"xml_http", "file_export"})


def build_sales_voucher_xml(
    *,
    tx: FinTransaction,
    lines: list[FinTransactionLine],
    party: Optional[FinParty],
    config: dict[str, Any],
) -> str:
    company = escape(str(config.get("company_name") or "").strip())
    sales_ledger = escape(str(config.get("sales_ledger") or "Sales").strip() or "Sales")
    party_name = escape(
        (party.name if party else None)
        or (party.source_ref if party else None)
        or "Cash"
    )
    date_str = tx.date.strftime("%Y%m%d")
    narr = escape(f"{tx.source_system}:{tx.source_ref}")
    amount = float(tx.amount or 0)

    line_xml = []
    for ln in lines:
        name = "Item"
        if getattr(ln, "item", None) is not None and getattr(ln.item, "name", None):
            name = ln.item.name
        qty = float(ln.qty or 0)
        rate = float(ln.rate or 0)
        line_amt = float(ln.line_amount or 0)
        line_xml.append(
            f"""
      <ALLINVENTORYENTRIES.LIST>
        <STOCKITEMNAME>{escape(name)}</STOCKITEMNAME>
        <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
        <RATE>{rate:.4f}</RATE>
        <AMOUNT>{line_amt:.2f}</AMOUNT>
        <ACTUALQTY>{qty:.4f}</ACTUALQTY>
        <BILLEDQTY>{qty:.4f}</BILLEDQTY>
      </ALLINVENTORYENTRIES.LIST>"""
        )
    inventory = "".join(line_xml) if line_xml else ""

    company_attr = f' SVCURRENTCOMPANY="{company}"' if company else ""
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<ENVELOPE>
  <HEADER>
    <TALLYREQUEST>Import Data</TALLYREQUEST>
  </HEADER>
  <BODY>
    <IMPORTDATA>
      <REQUESTDESC>
        <REPORTNAME>Vouchers</REPORTNAME>
        <STATICVARIABLES>
          <SVCURRENTCOMPANY>{company}</SVCURRENTCOMPANY>
        </STATICVARIABLES>
      </REQUESTDESC>
      <REQUESTDATA>
        <TALLYMESSAGE xmlns:UDF="TallyUDF">
          <VOUCHER VCHTYPE="Sales" ACTION="Create" OBJVIEW="Invoice Voucher View"{company_attr}>
            <DATE>{date_str}</DATE>
            <NARRATION>{narr}</NARRATION>
            <VOUCHERTYPENAME>Sales</VOUCHERTYPENAME>
            <PARTYLEDGERNAME>{party_name}</PARTYLEDGERNAME>
            <PERSISTEDVIEW>Invoice Voucher View</PERSISTEDVIEW>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{party_name}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>Yes</ISDEEMEDPOSITIVE>
              <AMOUNT>-{amount:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
            <ALLLEDGERENTRIES.LIST>
              <LEDGERNAME>{sales_ledger}</LEDGERNAME>
              <ISDEEMEDPOSITIVE>No</ISDEEMEDPOSITIVE>
              <AMOUNT>{amount:.2f}</AMOUNT>
            </ALLLEDGERENTRIES.LIST>
            {inventory}
          </VOUCHER>
        </TALLYMESSAGE>
      </REQUESTDATA>
    </IMPORTDATA>
  </BODY>
</ENVELOPE>
"""


class TallySink:
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

        existing = get_tx_sink_id(transaction, "tally_voucher_ref")
        if existing:
            append_outbound_log(
                db,
                tenant_id=tenant_id,
                transaction_id=transaction.id,
                system=SYSTEM,
                status="skipped",
                error_detail=f"mapping has tally_voucher_ref={existing}",
            )
            db.commit()
            return SyncResult(status="skipped", external_id=existing)

        mode = str(config.get("delivery_mode") or "").strip().lower()
        if mode not in VALID_MODES:
            msg = f"delivery_mode must be one of {sorted(VALID_MODES)}"
            append_outbound_log(
                db,
                tenant_id=tenant_id,
                transaction_id=transaction.id,
                system=SYSTEM,
                status="failed",
                error_detail=msg,
            )
            db.commit()
            return SyncResult(status="failed", error_detail=msg)

        # Skip non-sale types lightly for v1 (still log)
        txn_type = transaction.type.value if hasattr(transaction.type, "value") else str(transaction.type)
        if txn_type not in ("sale", "refund", "payment"):
            append_outbound_log(
                db,
                tenant_id=tenant_id,
                transaction_id=transaction.id,
                system=SYSTEM,
                status="skipped",
                error_detail=f"type {txn_type} not exported in v1",
            )
            db.commit()
            return SyncResult(status="skipped", error_detail=f"type {txn_type}")

        xml = build_sales_voucher_xml(
            tx=transaction,
            lines=lines or [],
            party=party,
            config=config,
        )
        voucher_ref = f"{transaction.source_system}:{transaction.source_ref}"

        try:
            if mode == "xml_http":
                result = self._push_http(config, credentials, xml, voucher_ref)
            else:
                result = self._push_file(config, xml, voucher_ref, tenant_id, transaction.id)

            if result.status == "success":
                set_tx_mapping(transaction, "tally_voucher_ref", voucher_ref)
                if result.external_id:
                    set_tx_mapping(transaction, "tally_export_path", result.external_id)

            append_outbound_log(
                db,
                tenant_id=tenant_id,
                transaction_id=transaction.id,
                system=SYSTEM,
                status=result.status,
                error_detail=result.error_detail,
            )
            db.commit()
            return result
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

    def _push_http(
        self,
        config: dict[str, Any],
        credentials: dict[str, Any],
        xml: str,
        voucher_ref: str,
    ) -> SyncResult:
        url = str(config.get("gateway_url") or credentials.get("gateway_url") or "").strip()
        if not url:
            return SyncResult(status="failed", error_detail="gateway_url required for xml_http")
        headers = {"Content-Type": "application/xml"}
        token = str(credentials.get("bearer_token") or "").strip()
        if token:
            headers["Authorization"] = f"Bearer {token}"
        client = self._client or httpx.Client(timeout=30.0)
        r = client.post(url, content=xml.encode("utf-8"), headers=headers)
        if r.status_code >= 400:
            return SyncResult(
                status="failed",
                error_detail=f"Tally gateway HTTP {r.status_code}: {(r.text or '')[:200]}",
            )
        return SyncResult(
            status="success",
            external_id=voucher_ref,
            meta={"mode": "xml_http", "http_status": r.status_code},
        )

    def _push_file(
        self,
        config: dict[str, Any],
        xml: str,
        voucher_ref: str,
        tenant_id: UUID,
        tx_id: UUID,
    ) -> SyncResult:
        export_dir = str(config.get("export_dir") or "").strip()
        if not export_dir:
            # Fallback to env default root
            export_dir = str(os.environ.get("TALLY_EXPORT_DIR") or "").strip()
        if not export_dir:
            return SyncResult(
                status="failed",
                error_detail="export_dir required for file_export (or TALLY_EXPORT_DIR env)",
            )
        root = Path(export_dir) / str(tenant_id)
        root.mkdir(parents=True, exist_ok=True)
        safe = voucher_ref.replace(":", "_").replace("/", "_")[:120]
        path = root / f"{safe}_{tx_id}.xml"
        path.write_text(xml, encoding="utf-8")
        return SyncResult(
            status="success",
            external_id=str(path),
            meta={"mode": "file_export", "path": str(path)},
        )
