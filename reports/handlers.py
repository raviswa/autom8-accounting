"""Read-only report handlers on canonical fin_* tables."""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from db.models import FinItem, FinTransaction, FinTransactionLine, TransactionType


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
    return {
        "report": "item_wise_sales",
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        "items": items,
        "total_amount": sum(i["line_amount"] for i in items),
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
    d_from, d_to = _parse_range(date_from, date_to)
    rows = _sales_q(db, tenant_id, d_from, d_to, source).all()
    # 7 x 24 matrix keyed by weekday 0=Mon
    matrix = [[0.0 for _ in range(24)] for _ in range(7)]
    total = 0.0
    for tx in rows:
        created = tx.created_at or datetime.combine(tx.date, datetime.min.time())
        if created.tzinfo:
            created = created.replace(tzinfo=None)
        wd = tx.date.weekday()
        hour = created.hour
        amt = float(tx.amount or 0)
        matrix[wd][hour] += amt
        total += amt
    return {
        "report": "hourly_sales_heatmap",
        "from": d_from.isoformat(),
        "to": d_to.isoformat(),
        "weekdays": ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"],
        "matrix": matrix,
        "total_amount": total,
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
    db: Session, tenant_id: uuid.UUID, **_
) -> dict[str, Any]:
    # Stock from stock_adjustment lines (qty deltas) — no perpetual inventory engine
    rows = (
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
    return {
        "report": "stock_summary",
        "items": [
            {"name": r[0], "sku": r[1], "qty_delta_sum": float(r[2])} for r in rows
        ],
        "note": "Sum of stock_adjustment qty deltas; not a full perpetual stock ledger.",
    }


def low_stock_alert(db: Session, tenant_id: uuid.UUID, **kwargs) -> dict[str, Any]:
    data = stock_summary(db, tenant_id, **kwargs)
    threshold = float(kwargs.get("threshold") or 5)
    low = [i for i in data["items"] if i["qty_delta_sum"] <= threshold]
    return {"report": "low_stock_alert", "threshold": threshold, "items": low}


def barcode_stock(db: Session, tenant_id: uuid.UUID, **kwargs) -> dict[str, Any]:
    data = stock_summary(db, tenant_id, **kwargs)
    return {
        "report": "barcode_stock",
        "items": [
            {"barcode": i["sku"], "name": i["name"], "qty_delta_sum": i["qty_delta_sum"]}
            for i in data["items"]
        ],
    }


HANDLERS = {
    "daily_sales_summary": daily_sales_summary,
    "weekly_sales_summary": weekly_sales_summary,
    "item_wise_sales": item_wise_sales,
    "day_book": day_book,
    "hourly_sales_heatmap": hourly_sales_heatmap,
    "counter_wise_sales": counter_wise_sales,
    "stock_summary": stock_summary,
    "low_stock_alert": low_stock_alert,
    "barcode_stock": barcode_stock,
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
