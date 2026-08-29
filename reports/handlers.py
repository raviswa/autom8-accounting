"""Read-only report handlers on canonical fin_* tables."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from db.models import FinItem, FinParty, FinTransaction, FinTransactionLine, TransactionType


def _parse_range(date_from: Optional[str], date_to: Optional[str]) -> tuple[date, date]:
    today = date.today()
    raw_to = (str(date_to).strip()[:10] if date_to else "") or ""
    raw_from = (str(date_from).strip()[:10] if date_from else "") or ""
    try:
        d_to = date.fromisoformat(raw_to) if raw_to else today
    except ValueError:
        d_to = today
    try:
        d_from = date.fromisoformat(raw_from) if raw_from else (d_to - timedelta(days=30))
    except ValueError:
        d_from = d_to - timedelta(days=30)
    if d_from > d_to:
        d_from, d_to = d_to, d_from
    return d_from, d_to


def _sales_q(db: Session, tenant_id: uuid.UUID, d_from: date, d_to: date, source: Optional[str]):
    q = db.query(FinTransaction).filter(
        FinTransaction.tenant_id == tenant_id,
        FinTransaction.type == TransactionType.sale,
        FinTransaction.date >= d_from,
        FinTransaction.date <= d_to,
    )
    if source:
        q = q.filter(FinTransaction.source_system == source)
    return q


def daily_sales_summary(
    db: Session, tenant_id: uuid.UUID, *, date_from=None, date_to=None, source=None, **_
) -> dict[str, Any]:
    d_from, d_to = _parse_range(date_from, date_to)
    rows = (
        _sales_q(db, tenant_id, d_from, d_to, source)
        .with_entities(
            FinTransaction.date,
            func.count(FinTransaction.id),
            func.coalesce(func.sum(FinTransaction.amount), 0),
            func.coalesce(func.sum(FinTransaction.tax_amount), 0),
        )
        .group_by(FinTransaction.date)
        .order_by(FinTransaction.date)
        .all()
    )
    days = [
        {
            "date": r[0].isoformat(),
            "txn_count": int(r[1]),
            "amount": float(r[2]),
            "tax_amount": float(r[3]),
        }
        for r in rows
    ]
    return {
        "report": "daily_sales_summary",
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        "days": days,
        "total_amount": sum(d["amount"] for d in days),
    }


def weekly_sales_summary(
    db: Session, tenant_id: uuid.UUID, *, date_from=None, date_to=None, source=None, **_
) -> dict[str, Any]:
    d_from, d_to = _parse_range(date_from, date_to)
    rows = _sales_q(db, tenant_id, d_from, d_to, source).all()
    buckets: dict[str, dict[str, Any]] = {}
    for tx in rows:
        iso = tx.date.isocalendar()
        key = f"{iso.year}-W{iso.week:02d}"
        b = buckets.setdefault(key, {"week": key, "txn_count": 0, "amount": 0.0})
        b["txn_count"] += 1
        b["amount"] += float(tx.amount or 0)
    weeks = sorted(buckets.values(), key=lambda x: x["week"])
    return {
        "report": "weekly_sales_summary",
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        "weeks": weeks,
        "total_amount": sum(w["amount"] for w in weeks),
    }


def item_wise_sales(
    db: Session, tenant_id: uuid.UUID, *, date_from=None, date_to=None, source=None, **_
) -> dict[str, Any]:
    d_from, d_to = _parse_range(date_from, date_to)
    q = (
        db.query(
            FinItem.name,
            FinItem.source_ref,
            func.coalesce(func.sum(FinTransactionLine.qty), 0),
            func.coalesce(func.sum(FinTransactionLine.line_amount), 0),
            func.coalesce(func.sum(FinTransactionLine.line_tax), 0),
        )
        .join(FinTransactionLine, FinTransactionLine.item_id == FinItem.id)
        .join(FinTransaction, FinTransaction.id == FinTransactionLine.transaction_id)
        .filter(
            FinTransaction.tenant_id == tenant_id,
            FinTransaction.type == TransactionType.sale,
            FinTransaction.date >= d_from,
            FinTransaction.date <= d_to,
        )
    )
    if source:
        q = q.filter(FinTransaction.source_system == source)
    rows = q.group_by(FinItem.name, FinItem.source_ref).order_by(func.sum(FinTransactionLine.line_amount).desc()).limit(200).all()
    items = [
        {
            "name": r[0],
            "sku": r[1],
            "qty": float(r[2]),
            "line_amount": float(r[3]),
            "line_tax": float(r[4]),
        }
        for r in rows
    ]
    # Match daily/header totals: amount = taxable + tax
    total_amount = sum(i["line_amount"] + i["line_tax"] for i in items)
    return {
        "report": "item_wise_sales",
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        "items": items,
        "total_amount": total_amount,
        "total_tax": sum(i["line_tax"] for i in items),
    }


def day_book(
    db: Session, tenant_id: uuid.UUID, *, date_from=None, date_to=None, source=None, **_
) -> dict[str, Any]:
    d_from, d_to = _parse_range(date_from, date_to)
    # Default day book to single day if wide range not intended — still honor range
    q = db.query(FinTransaction).filter(
        FinTransaction.tenant_id == tenant_id,
        FinTransaction.date >= d_from,
        FinTransaction.date <= d_to,
    )
    if source:
        q = q.filter(FinTransaction.source_system == source)
    rows = q.order_by(FinTransaction.date, FinTransaction.created_at).limit(500).all()
    entries = [
        {
            "id": str(t.id),
            "date": t.date.isoformat(),
            "type": t.type.value if hasattr(t.type, "value") else str(t.type),
            "source_ref": t.source_ref,
            "amount": float(t.amount or 0),
            "tax_amount": float(t.tax_amount or 0),
            "payment_mode": t.payment_mode,
            "category": t.category,
        }
        for t in rows
    ]
    return {
        "report": "day_book",
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        "entries": entries,
        "total_amount": sum(e["amount"] for e in entries),
    }


def hourly_sales_heatmap(
    db: Session, tenant_id: uuid.UUID, *, date_from=None, date_to=None, source=None, **_
) -> dict[str, Any]:
    from zoneinfo import ZoneInfo

    d_from, d_to = _parse_range(date_from, date_to)
    rows = _sales_q(db, tenant_id, d_from, d_to, source).all()
    # 7 x 24 matrix keyed by weekday 0=Mon — bucket by sale time in Asia/Kolkata
    ist = ZoneInfo("Asia/Kolkata")
    matrix = [[0.0 for _ in range(24)] for _ in range(7)]
    total = 0.0
    timed = 0
    untimed = 0
    for tx in rows:
        amt = float(tx.amount or 0)
        total += amt
        when = getattr(tx, "occurred_at", None)
        if when is None:
            # No sale timestamp (legacy ingest) — skip hour bucket rather than use ingest created_at
            untimed += 1
            continue
        if when.tzinfo is None:
            when = when.replace(tzinfo=ZoneInfo("UTC"))
        local = when.astimezone(ist)
        wd = local.weekday()
        hour = local.hour
        matrix[wd][hour] += amt
        timed += 1
    return {
        "report": "hourly_sales_heatmap",
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "matrix": matrix,
        "total_amount": total,
        "timed_txn_count": timed,
        "untimed_txn_count": untimed,
        "note": (
            "Hours use sale time (Asia/Kolkata). "
            "Re-run ledger backfill after deploy if many cells are empty — older rows lacked occurred_at."
            if untimed
            else "Hours use sale time (Asia/Kolkata)."
        ),
    }


def counter_wise_sales(
    db: Session, tenant_id: uuid.UUID, *, date_from=None, date_to=None, source=None, **_
) -> dict[str, Any]:
    d_from, d_to = _parse_range(date_from, date_to)
    rows = _sales_q(db, tenant_id, d_from, d_to, source).all()
    counters: dict[str, dict[str, Any]] = {}
    for tx in rows:
        # category may encode lob/service; payment_mode as counter proxy when set
        key = tx.payment_mode or tx.category or "default"
        b = counters.setdefault(key, {"counter": key, "txn_count": 0, "amount": 0.0})
        b["txn_count"] += 1
        b["amount"] += float(tx.amount or 0)
    counter_rows = sorted(counters.values(), key=lambda x: -x["amount"])
    return {
        "report": "counter_wise_sales",
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        "counters": counter_rows,
        "total_amount": sum(c["amount"] for c in counter_rows),
    }


def stock_summary(
    db: Session, tenant_id: uuid.UUID, *, date_from=None, date_to=None, source=None, **_
) -> dict[str, Any]:
    """
    Prefer stock_adjustment qty deltas when present.
    Otherwise fall back to units sold from sale lines in the date range
    (restaurants rarely ingest stock_adjustment yet).
    """
    d_from, d_to = _parse_range(date_from, date_to)
    adj_rows = (
        db.query(
            FinItem.name,
            FinItem.source_ref,
            func.coalesce(func.sum(FinTransactionLine.qty), 0),
        )
        .join(FinTransactionLine, FinTransactionLine.item_id == FinItem.id)
        .join(FinTransaction, FinTransaction.id == FinTransactionLine.transaction_id)
        .filter(
            FinTransaction.tenant_id == tenant_id,
            FinTransaction.type == TransactionType.stock_adjustment,
        )
        .group_by(FinItem.name, FinItem.source_ref)
        .all()
    )
    if adj_rows:
        items = [
            {
                "name": r[0],
                "sku": r[1],
                "barcode": r[1],
                "qty": float(r[2]),
                "qty_delta_sum": float(r[2]),
            }
            for r in adj_rows
        ]
        return {
            "report": "stock_summary",
            "from": d_from.isoformat(),
            "to": d_to.isoformat(),
            "items": items,
            "mode": "stock_adjustment",
            "total_amount": sum(abs(i["qty"]) for i in items),
            "note": "Sum of stock_adjustment qty deltas.",
        }

    # Fallback: units sold in range (same filters as item-wise)
    q = (
        db.query(
            FinItem.name,
            FinItem.source_ref,
            func.coalesce(func.sum(FinTransactionLine.qty), 0),
            func.coalesce(func.sum(FinTransactionLine.line_amount), 0)
            + func.coalesce(func.sum(FinTransactionLine.line_tax), 0),
        )
        .join(FinTransactionLine, FinTransactionLine.item_id == FinItem.id)
        .join(FinTransaction, FinTransaction.id == FinTransactionLine.transaction_id)
        .filter(
            FinTransaction.tenant_id == tenant_id,
            FinTransaction.type == TransactionType.sale,
            FinTransaction.date >= d_from,
            FinTransaction.date <= d_to,
        )
    )
    if source:
        q = q.filter(FinTransaction.source_system == source)
    sale_rows = (
        q.group_by(FinItem.name, FinItem.source_ref)
        .order_by(func.sum(FinTransactionLine.qty).desc())
        .limit(500)
        .all()
    )
    items = [
        {
            "name": r[0],
            "sku": r[1],
            "barcode": r[1],
            "qty": float(r[2]),
            "qty_sold": float(r[2]),
            "amount": float(r[3]),
        }
        for r in sale_rows
    ]
    return {
        "report": "stock_summary",
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        "items": items,
        "mode": "units_sold",
        "total_amount": sum(i["amount"] for i in items),
        "note": "No stock adjustments yet — showing units sold in this date range (SKU as barcode).",
    }


def low_stock_alert(db: Session, tenant_id: uuid.UUID, **kwargs) -> dict[str, Any]:
    data = stock_summary(db, tenant_id, **kwargs)
    threshold = float(kwargs.get("threshold") or 5)
    # For units_sold mode, "low stock" isn't meaningful — show slow movers (qty <= threshold)
    low = [i for i in data["items"] if float(i.get("qty") or 0) <= threshold]
    return {
        "report": "low_stock_alert",
        "from": data.get("from"),
        "to": data.get("to"),
        "threshold": threshold,
        "items": low,
        "mode": data.get("mode"),
        "note": data.get("note"),
        "total_amount": sum(float(i.get("amount") or i.get("qty") or 0) for i in low),
    }


def barcode_stock(db: Session, tenant_id: uuid.UUID, **kwargs) -> dict[str, Any]:
    data = stock_summary(db, tenant_id, **kwargs)
    items = [
        {
            "barcode": i.get("barcode") or i.get("sku") or "—",
            "name": i["name"],
            "sku": i.get("sku"),
            "qty": float(i.get("qty") or 0),
            "amount": float(i["amount"]) if i.get("amount") is not None else None,
        }
        for i in data["items"]
    ]
    return {
        "report": "barcode_stock",
        "from": data.get("from"),
        "to": data.get("to"),
        "items": items,
        "mode": data.get("mode"),
        "total_amount": data.get("total_amount"),
        "note": data.get("note"),
    }


def top_skus(db: Session, tenant_id: uuid.UUID, **kwargs) -> dict[str, Any]:
    data = item_wise_sales(db, tenant_id, **kwargs)
    items = sorted(data["items"], key=lambda x: -(x.get("line_amount", 0) + x.get("line_tax", 0)))[:25]
    return {
        "report": "top_skus",
        "from": data.get("from"),
        "to": data.get("to"),
        "items": items,
        "total_amount": sum(i["line_amount"] + i["line_tax"] for i in items),
    }


def multi_source_sales(
    db: Session, tenant_id: uuid.UUID, *, date_from=None, date_to=None, source=None, **_
) -> dict[str, Any]:
    d_from, d_to = _parse_range(date_from, date_to)
    rows = (
        _sales_q(db, tenant_id, d_from, d_to, source)
        .with_entities(
            FinTransaction.source_system,
            func.count(FinTransaction.id),
            func.coalesce(func.sum(FinTransaction.amount), 0),
            func.coalesce(func.sum(FinTransaction.tax_amount), 0),
        )
        .group_by(FinTransaction.source_system)
        .all()
    )
    sources = [
        {
            "source": r[0] or "unknown",
            "txn_count": int(r[1]),
            "amount": float(r[2]),
            "tax_amount": float(r[3]),
        }
        for r in rows
    ]
    return {
        "report": "multi_source_sales",
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        "sources": sources,
        "total_amount": sum(s["amount"] for s in sources),
    }


def gst_export(
    db: Session, tenant_id: uuid.UUID, *, date_from=None, date_to=None, source=None, **_
) -> dict[str, Any]:
    d_from, d_to = _parse_range(date_from, date_to)
    rows = _sales_q(db, tenant_id, d_from, d_to, source).all()
    entries = [
        {
            "date": t.date.isoformat(),
            "source_ref": t.source_ref,
            "amount": float(t.amount or 0),
            "tax_amount": float(t.tax_amount or 0),
            "cgst": float((t.tax_breakdown or {}).get("cgst") or 0),
            "sgst": float((t.tax_breakdown or {}).get("sgst") or 0),
            "igst": float((t.tax_breakdown or {}).get("igst") or 0),
        }
        for t in rows
    ]
    return {
        "report": "gst_export",
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        "entries": entries,
        "total_amount": sum(e["amount"] for e in entries),
        "total_tax": sum(e["tax_amount"] for e in entries),
    }


def cashier_wise_sales(db: Session, tenant_id: uuid.UUID, **kwargs) -> dict[str, Any]:
    data = counter_wise_sales(db, tenant_id, **kwargs)
    return {
        "report": "cashier_wise_sales",
        "from": data.get("from"),
        "to": data.get("to"),
        "counters": [
            {"cashier": c["counter"], "txn_count": c["txn_count"], "amount": c["amount"]}
            for c in data["counters"]
        ],
        "total_amount": data.get("total_amount"),
        "note": "Grouped by payment_mode / category until cashier identity is wired.",
    }


def pos_returns(
    db: Session, tenant_id: uuid.UUID, *, date_from=None, date_to=None, source=None, **_
) -> dict[str, Any]:
    d_from, d_to = _parse_range(date_from, date_to)
    q = db.query(FinTransaction).filter(
        FinTransaction.tenant_id == tenant_id,
        FinTransaction.type == TransactionType.refund,
        FinTransaction.date >= d_from,
        FinTransaction.date <= d_to,
    )
    if source:
        q = q.filter(FinTransaction.source_system == source)
    rows = q.order_by(FinTransaction.date.desc()).limit(500).all()
    entries = [
        {
            "date": t.date.isoformat(),
            "source_ref": t.source_ref,
            "amount": float(t.amount or 0),
            "tax_amount": float(t.tax_amount or 0),
            "payment_mode": t.payment_mode,
        }
        for t in rows
    ]
    return {
        "report": "pos_returns",
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        "entries": entries,
        "total_amount": sum(e["amount"] for e in entries),
    }


def customer_clv(
    db: Session, tenant_id: uuid.UUID, *, date_from=None, date_to=None, source=None, **_
) -> dict[str, Any]:
    d_from, d_to = _parse_range(date_from, date_to)
    rows = (
        db.query(
            FinParty.name,
            FinParty.source_ref,
            func.count(FinTransaction.id),
            func.coalesce(func.sum(FinTransaction.amount), 0),
        )
        .join(FinTransaction, FinTransaction.party_id == FinParty.id)
        .filter(
            FinTransaction.tenant_id == tenant_id,
            FinTransaction.type == TransactionType.sale,
            FinTransaction.date >= d_from,
            FinTransaction.date <= d_to,
        )
        .group_by(FinParty.name, FinParty.source_ref)
        .order_by(func.sum(FinTransaction.amount).desc())
        .limit(200)
        .all()
    )
    if source:
        pass  # party join already filtered by sales q scope via date; source optional later
    customers = [
        {
            "name": r[0],
            "phone": r[1],
            "txn_count": int(r[2]),
            "amount": float(r[3]),
        }
        for r in rows
    ]
    return {
        "report": "customer_clv",
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        "customers": customers,
        "total_amount": sum(c["amount"] for c in customers),
        "note": "Spend in selected range by customer (ledger party).",
    }


def customer_rfm(db: Session, tenant_id: uuid.UUID, **kwargs) -> dict[str, Any]:
    data = customer_clv(db, tenant_id, **kwargs)
    return {
        "report": "customer_rfm",
        "from": data.get("from"),
        "to": data.get("to"),
        "customers": data["customers"],
        "total_amount": data.get("total_amount"),
        "note": "Simplified RFM preview — frequency + monetary from sales in range.",
    }


def sink_sync_status(db: Session, tenant_id: uuid.UUID, **_) -> dict[str, Any]:
    from db.models import FinSyncLog

    rows = (
        db.query(FinSyncLog)
        .filter(FinSyncLog.tenant_id == tenant_id)
        .order_by(FinSyncLog.attempted_at.desc())
        .limit(100)
        .all()
    )
    entries = [
        {
            "system": r.system,
            "direction": r.direction,
            "status": r.status,
            "error_detail": r.error_detail,
            "attempted_at": r.attempted_at.isoformat() if r.attempted_at else None,
        }
        for r in rows
    ]
    return {
        "report": "sink_sync_status",
        "entries": entries,
        "total_amount": len(entries),
        "note": "Recent inbound/outbound sync log rows.",
    }


def _empty_named(report: str, *, note: str, date_from=None, date_to=None) -> dict[str, Any]:
    d_from, d_to = _parse_range(date_from, date_to)
    return {
        "report": report,
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        "items": [],
        "entries": [],
        "total_amount": 0,
        "note": note,
        "message": note,
    }


def churn_risk(db: Session, tenant_id: uuid.UUID, **kwargs) -> dict[str, Any]:
    return _empty_named(
        "churn_risk",
        note="Churn model not wired yet — unlocks with marketing analytics.",
        date_from=kwargs.get("date_from"),
        date_to=kwargs.get("date_to"),
    )


def browsed_without_buying(db: Session, tenant_id: uuid.UUID, **kwargs) -> dict[str, Any]:
    return _empty_named(
        "browsed_without_buying",
        note="Needs webcart browse events — not in ledger yet.",
        date_from=kwargs.get("date_from"),
        date_to=kwargs.get("date_to"),
    )


def refill_responses(db: Session, tenant_id: uuid.UUID, **kwargs) -> dict[str, Any]:
    return _empty_named(
        "refill_responses",
        note="Needs refill campaign responses — not in ledger yet.",
        date_from=kwargs.get("date_from"),
        date_to=kwargs.get("date_to"),
    )


def product_affinity(db: Session, tenant_id: uuid.UUID, **kwargs) -> dict[str, Any]:
    return _empty_named(
        "product_affinity",
        note="Basket affinity coming soon — use item-wise sales for now.",
        date_from=kwargs.get("date_from"),
        date_to=kwargs.get("date_to"),
    )


def purchase_register(db: Session, tenant_id: uuid.UUID, **kwargs) -> dict[str, Any]:
    return _empty_named(
        "purchase_register",
        note="No purchase transactions ingested yet.",
        date_from=kwargs.get("date_from"),
        date_to=kwargs.get("date_to"),
    )


def inventory_aging(db: Session, tenant_id: uuid.UUID, **kwargs) -> dict[str, Any]:
    return _empty_named(
        "inventory_aging",
        note="Needs perpetual stock receipts — use stock summary (units sold) for now.",
        date_from=kwargs.get("date_from"),
        date_to=kwargs.get("date_to"),
    )


def dead_stock(db: Session, tenant_id: uuid.UUID, **kwargs) -> dict[str, Any]:
    return _empty_named(
        "dead_stock",
        note="Needs stock receipts + zero sales window — not available yet.",
        date_from=kwargs.get("date_from"),
        date_to=kwargs.get("date_to"),
    )


def outstanding_receivables(db: Session, tenant_id: uuid.UUID, **kwargs) -> dict[str, Any]:
    return _empty_named(
        "outstanding_receivables",
        note="Ledger currently stores paid sales only — no open receivables.",
        date_from=kwargs.get("date_from"),
        date_to=kwargs.get("date_to"),
    )


def outstanding_payables(db: Session, tenant_id: uuid.UUID, **kwargs) -> dict[str, Any]:
    return _empty_named(
        "outstanding_payables",
        note="No payables ingested yet.",
        date_from=kwargs.get("date_from"),
        date_to=kwargs.get("date_to"),
    )


def shift_report(db: Session, tenant_id: uuid.UUID, **kwargs) -> dict[str, Any]:
    return _empty_named(
        "shift_report",
        note="Shift open/close events not in ledger yet — use counter-wise sales.",
        date_from=kwargs.get("date_from"),
        date_to=kwargs.get("date_to"),
    )


def cash_drawer(db: Session, tenant_id: uuid.UUID, **kwargs) -> dict[str, Any]:
    return _empty_named(
        "cash_drawer",
        note="Cash drawer events not in ledger yet.",
        date_from=kwargs.get("date_from"),
        date_to=kwargs.get("date_to"),
    )


def admin_audit(db: Session, tenant_id: uuid.UUID, **kwargs) -> dict[str, Any]:
    return _empty_named(
        "admin_audit",
        note="Admin audit trail lives in Autom8 ops DB — not mirrored to ledger yet.",
        date_from=kwargs.get("date_from"),
        date_to=kwargs.get("date_to"),
    )


def gl_passthrough_stub(report: str):
    def _handler(db: Session, tenant_id: uuid.UUID, **kwargs) -> dict[str, Any]:
        return _empty_named(
            report,
            note="Requires Zoho/Tally GL passthrough — connect a sink in Integrations.",
            date_from=kwargs.get("date_from"),
            date_to=kwargs.get("date_to"),
        )

    return _handler


HANDLERS = {
    "daily_sales_summary": daily_sales_summary,
    "weekly_sales_summary": weekly_sales_summary,
    "item_wise_sales": item_wise_sales,
    "top_skus": top_skus,
    "day_book": day_book,
    "hourly_sales_heatmap": hourly_sales_heatmap,
    "counter_wise_sales": counter_wise_sales,
    "stock_summary": stock_summary,
    "low_stock_alert": low_stock_alert,
    "barcode_stock": barcode_stock,
    "multi_source_sales": multi_source_sales,
    "gst_export": gst_export,
    "cashier_wise_sales": cashier_wise_sales,
    "pos_returns": pos_returns,
    "customer_clv": customer_clv,
    "customer_rfm": customer_rfm,
    "sink_sync_status": sink_sync_status,
    "churn_risk": churn_risk,
    "browsed_without_buying": browsed_without_buying,
    "refill_responses": refill_responses,
    "product_affinity": product_affinity,
    "purchase_register": purchase_register,
    "inventory_aging": inventory_aging,
    "dead_stock": dead_stock,
    "outstanding_receivables": outstanding_receivables,
    "outstanding_payables": outstanding_payables,
    "shift_report": shift_report,
    "cash_drawer": cash_drawer,
    "admin_audit": admin_audit,
    "gstr1": gl_passthrough_stub("gstr1"),
    "gstr3b": gl_passthrough_stub("gstr3b"),
    "inventory_valuation": gl_passthrough_stub("inventory_valuation"),
    "cash_flow": gl_passthrough_stub("cash_flow"),
    "trial_balance": gl_passthrough_stub("trial_balance"),
    "balance_sheet": gl_passthrough_stub("balance_sheet"),
}


def aggregate_preview(full: dict[str, Any]) -> dict[str, Any]:
    """Strip line-level detail for 423 Locked responses."""
    preview = {"report": full.get("report"), "locked": True, "preview": True}
    if "total_amount" in full:
        preview["total_amount"] = full["total_amount"]
    if "days" in full:
        preview["day_count"] = len(full["days"])
        preview["total_amount"] = full.get("total_amount")
    if "weeks" in full:
        preview["week_count"] = len(full["weeks"])
    if "items" in full:
        preview["item_count"] = len(full["items"])
    if "entries" in full:
        preview["entry_count"] = len(full["entries"])
    if "counters" in full:
        preview["counter_count"] = len(full["counters"])
    if "matrix" in full:
        preview["heatmap_max"] = max((max(row) for row in full["matrix"]), default=0)
    return preview
