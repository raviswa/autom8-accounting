"""Report registry + free/paid gating (rule 6)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Optional


@dataclass(frozen=True)
class ReportDef:
    id: int
    slug: str
    name: str
    category: str
    tier: str  # free | paid
    requires_gl: bool = False
    description: str = ""


# Free (9) — operational teaser only
FREE_REPORTS: list[ReportDef] = [
    ReportDef(1, "daily_sales_summary", "Daily sales summary", "sales", "free"),
    ReportDef(2, "weekly_sales_summary", "Weekly sales summary", "sales", "free"),
    ReportDef(4, "item_wise_sales", "Item-wise sales", "sales", "free"),
    ReportDef(26, "stock_summary", "Stock summary", "inventory", "free"),
    ReportDef(30, "low_stock_alert", "Low-stock / reorder alert", "inventory", "free"),
    ReportDef(36, "barcode_stock", "Barcode-wise stock", "inventory", "free"),
    ReportDef(45, "day_book", "Day book", "sales", "free"),
    ReportDef(78, "counter_wise_sales", "Counter-wise sales (POS)", "pos", "free"),
    ReportDef(83, "hourly_sales_heatmap", "Hourly sales heatmap", "sales", "free"),
]

# Paid category placeholders (full 88 catalog can expand IDs later)
PAID_PLACEHOLDERS: list[ReportDef] = [
    ReportDef(15, "top_skus", "Top SKUs / top-selling items", "sales", "paid"),
    ReportDef(101, "customer_rfm", "Customer RFM / retention", "analytics", "paid"),
    ReportDef(102, "customer_clv", "Customer lifetime value", "analytics", "paid"),
    ReportDef(103, "churn_risk", "Churn risk", "analytics", "paid"),
    ReportDef(104, "browsed_without_buying", "Browsed without buying", "analytics", "paid"),
    ReportDef(105, "refill_responses", "Refill responses", "analytics", "paid"),
    ReportDef(106, "product_affinity", "Product affinity", "analytics", "paid"),
    ReportDef(107, "multi_source_sales", "Multi-source sales", "sales", "paid"),
    ReportDef(110, "gst_export", "GST export", "gst", "paid", requires_gl=False),
    ReportDef(111, "gstr1", "GSTR-1 style summary", "gst", "paid", requires_gl=True),
    ReportDef(112, "gstr3b", "GSTR-3B style summary", "gst", "paid", requires_gl=True),
    ReportDef(120, "purchase_register", "Purchase register", "purchase", "paid"),
    ReportDef(121, "inventory_valuation", "Inventory valuation", "inventory", "paid", requires_gl=True),
    ReportDef(122, "inventory_aging", "Inventory aging", "inventory", "paid"),
    ReportDef(123, "dead_stock", "Dead stock", "inventory", "paid"),
    ReportDef(130, "cash_flow", "Cash flow", "cash_flow", "paid", requires_gl=True),
    ReportDef(131, "outstanding_receivables", "Outstanding receivables / aging", "outstanding", "paid"),
    ReportDef(132, "outstanding_payables", "Outstanding payables", "outstanding", "paid"),
    ReportDef(140, "trial_balance", "Trial balance", "financial_statements", "paid", requires_gl=True),
    ReportDef(141, "profit_and_loss", "Profit & loss", "financial_statements", "paid", requires_gl=True),
    ReportDef(142, "balance_sheet", "Balance sheet", "financial_statements", "paid", requires_gl=True),
    ReportDef(150, "cashier_wise_sales", "Cashier-wise sales", "pos", "paid"),
    ReportDef(151, "shift_report", "Shift report", "pos", "paid"),
    ReportDef(152, "cash_drawer", "Cash drawer", "pos", "paid"),
    ReportDef(153, "pos_returns", "POS returns", "pos", "paid"),
    ReportDef(160, "admin_audit", "Admin / security audit", "admin_security", "paid"),
    ReportDef(161, "sink_sync_status", "Accounting sink sync status", "admin_security", "paid"),
]

REGISTRY: dict[str, ReportDef] = {r.slug: r for r in FREE_REPORTS + PAID_PLACEHOLDERS}
FREE_SLUGS = {r.slug for r in FREE_REPORTS}


def list_reports() -> list[dict[str, Any]]:
    return [
        {
            "id": r.id,
            "slug": r.slug,
            "name": r.name,
            "category": r.category,
            "tier": r.tier,
            "requires_gl": r.requires_gl,
            "description": r.description,
        }
        for r in sorted(REGISTRY.values(), key=lambda x: x.id)
    ]


def get_report(slug: str) -> Optional[ReportDef]:
    return REGISTRY.get(slug)
