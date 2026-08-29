"""Autom8 / Munafe source adapter — only place Autom8-specific translation lives."""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Optional
from uuid import UUID

from sources.base import NormalizedLine, NormalizedParty, NormalizedTransaction

SOURCE_SYSTEM = "autom8"

EVENT_TO_TYPE = {
    "order.created": "sale",
    "order.paid": "sale",
    "refund.issued": "refund",
    "stock.adjustment": "stock_adjustment",
}


def _d(value: Any, default: str = "0") -> Decimal:
    try:
        if value is None or value == "":
            return Decimal(default)
        return Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError):
        return Decimal(default)


def _parse_datetime(raw: dict[str, Any]) -> Optional[datetime]:
    for key in ("occurred_at", "date", "paid_at", "created_at"):
        val = raw.get(key)
        if not val:
            continue
        if isinstance(val, datetime):
            return val if val.tzinfo else val.replace(tzinfo=timezone.utc)
        if isinstance(val, date) and not isinstance(val, datetime):
            return datetime(val.year, val.month, val.day, tzinfo=timezone.utc)
        s = str(val).replace("Z", "+00:00")
        try:
            dt = datetime.fromisoformat(s)
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
        except ValueError:
            try:
                d = date.fromisoformat(s[:10])
                return datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
            except ValueError:
                continue
    return None


def _parse_date(raw: dict[str, Any]) -> date:
    dt = _parse_datetime(raw)
    if dt:
        return dt.date()
    return date.today()


def _party(raw: dict[str, Any]) -> Optional[NormalizedParty]:
    party = raw.get("party") or {}
    if not isinstance(party, dict):
        return None
    ref = str(party.get("source_ref") or party.get("phone") or party.get("id") or "").strip()
    name = str(party.get("name") or "Customer").strip() or "Customer"
    if not ref:
        # Anonymous walk-in — still useful for category tagging
        if raw.get("event") in ("stock.adjustment",):
            return None
        ref = "anonymous"
    gstin = party.get("gstin")
    return NormalizedParty(
        source_ref=ref,
        name=name,
        gstin=str(gstin).strip() if gstin else None,
    )


def _lines(raw: dict[str, Any]) -> list[NormalizedLine]:
    out: list[NormalizedLine] = []
    for i, line in enumerate(raw.get("lines") or []):
        if not isinstance(line, dict):
            continue
        qty = _d(line.get("qty") if line.get("qty") is not None else line.get("quantity"), "1")
        rate = _d(line.get("rate") if line.get("rate") is not None else line.get("unit_price"))
        line_amount = _d(
            line.get("line_amount") if line.get("line_amount") is not None else line.get("line_total"),
            str(qty * rate),
        )
        line_tax = _d(line.get("line_tax") if line.get("line_tax") is not None else line.get("gst_amount"))
        item_ref = str(
            line.get("item_source_ref")
            or line.get("menu_item_id")
            or line.get("item_sku")
            or line.get("sku")
            or f"line-{i}"
        ).strip()
        name = str(line.get("item_name") or line.get("name") or item_ref).strip() or item_ref
        hsn = line.get("hsn_sac") or line.get("hsn_sac_code")
        tax_rate = line.get("tax_rate") if line.get("tax_rate") is not None else line.get("gst_rate")
        out.append(
            NormalizedLine(
                item_source_ref=item_ref,
                item_name=name,
                qty=qty,
                rate=rate,
                line_amount=line_amount.quantize(Decimal("0.01")),
                line_tax=line_tax.quantize(Decimal("0.01")),
                hsn_sac=str(hsn).strip() if hsn else None,
                tax_rate=_d(tax_rate) if tax_rate is not None and tax_rate != "" else None,
            )
        )
    return out


def _category(raw: dict[str, Any]) -> Optional[str]:
    """Map Autom8 LOB / channel into a generic category tag (no Autom8 enums leak out)."""
    if raw.get("category"):
        return str(raw["category"])[:128]
    lob = str(raw.get("lob") or raw.get("lob_type") or "").strip().lower()
    channel = str(raw.get("channel") or raw.get("service_type") or "").strip().lower()
    parts = [p for p in (lob, channel) if p]
    return "/".join(parts)[:128] if parts else None


def translate(raw_event: dict[str, Any]) -> NormalizedTransaction:
    if not isinstance(raw_event, dict):
        raise ValueError("raw_event must be an object")

    event = str(raw_event.get("event") or "").strip()
    txn_type = EVENT_TO_TYPE.get(event) or str(raw_event.get("type") or "").strip()
    if txn_type not in EVENT_TO_TYPE.values():
        raise ValueError(f"unsupported event/type: {event or txn_type}")

    tenant_raw = raw_event.get("tenant_id") or raw_event.get("restaurant_id")
    if not tenant_raw:
        raise ValueError("tenant_id required")
    tenant_id = UUID(str(tenant_raw))

    source_ref = str(raw_event.get("source_ref") or "").strip()
    if not source_ref:
        raise ValueError("source_ref required")

    lines = _lines(raw_event)
    amount = _d(raw_event.get("amount"))
    tax_amount = _d(raw_event.get("tax_amount"))

    # If amount omitted but lines present, derive header from lines
    if amount == 0 and lines and event != "stock.adjustment":
        amount = sum((ln.line_amount + ln.line_tax for ln in lines), Decimal("0"))
        if tax_amount == 0:
            tax_amount = sum((ln.line_tax for ln in lines), Decimal("0"))

    # Stock adjustments may be qty-only with zero money
    if event == "stock.adjustment" and not lines:
        raise ValueError("stock.adjustment requires lines")

    tax_breakdown = raw_event.get("tax_breakdown")
    if not isinstance(tax_breakdown, dict):
        tax_breakdown = {}

    return NormalizedTransaction(
        tenant_id=tenant_id,
        source_system=SOURCE_SYSTEM,
        source_ref=source_ref,
        txn_date=_parse_date(raw_event),
        occurred_at=_parse_datetime(raw_event),
        txn_type=txn_type,
        amount=amount.quantize(Decimal("0.01")),
        tax_amount=tax_amount.quantize(Decimal("0.01")),
        tax_breakdown=tax_breakdown,
        category=_category(raw_event),
        payment_mode=(
            str(raw_event["payment_mode"]).strip()[:64]
            if raw_event.get("payment_mode")
            else None
        ),
        party=_party(raw_event),
        lines=lines,
    )


class Autom8SourceAdapter:
    def translate(self, raw_event: dict[str, Any]) -> NormalizedTransaction:
        return translate(raw_event)
