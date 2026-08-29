"""Report registry + free/paid gating (rule 6).

Demo/free tenants get almost the full catalog unlocked. A single paid teaser
(`profit_and_loss`) stays locked to demonstrate upgrade.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class ReportDef:
    id: int
    slug: str
    name: str
    category: str
    tier: str  # free | paid
    requires_gl: bool = False
    description: str = ""


# Operational + analytics reports available on free/demo (handlers may be thin)
FREE_REPORTS: list[ReportDef] = [
    ReportDef(1, "daily_sales_summary", "Daily sales summary", "sales", "free"),
    ReportDef(2, "weekly_sales_summary", "Weekly sales summary", "sales", "free"),
    ReportDef(4, "item_wise_sales", "Item-wise sales", "sales", "free"),
    ReportDef(15, "top_skus", "Top SKUs / top-selling items", "sales", "free"),
    ReportDef(26, "stock_summary", "Stock summary", "inventory", "free"),
    ReportDef(30, "low_stock_alert", "Low-stock / reorder alert", "inventory", "free"),
    ReportDef(36, "barcode_stock", "Barcode-wise stock", "inventory", "free"),
    ReportDef(45, "day_book", "Day book", "sales", "free"),
    ReportDef(78, "counter_wise_sales", "Counter-wise sales (POS)", "pos", "free"),
    ReportDef(83, "hourly_sales_heatmap", "Hourly sales heatmap", "sales", "free"),
    ReportDef(101, "customer_rfm", "Customer RFM / retention", "analytics", "free"),
    ReportDef(102, "customer_clv", "Customer lifetime value", "analytics", "free"),
    ReportDef(103, "churn_risk", "Churn risk", "analytics", "free"),
    ReportDef(104, "browsed_without_buying", "Browsed without buying", "analytics", "free"),
    ReportDef(105, "refill_responses", "Refill responses", "analytics", "free"),
    ReportDef(106, "product_affinity", "Product affinity", "analytics", "free"),
    ReportDef(107, "multi_source_sales", "Multi-source sales", "sales", "free"),
    ReportDef(110, "gst_export", "GST export", "gst", "free"),
    ReportDef(111, "gstr1", "GSTR-1 style summary", "gst", "free", requires_gl=True),
    ReportDef(112, "gstr3b", "GSTR-3B style summary", "gst", "free", requires_gl=True),
    ReportDef(120, "purchase_register", "Purchase register", "purchase", "free"),
    ReportDef(121, "inventory_valuation", "Inventory valuation", "inventory", "free", requires_gl=True),
    ReportDef(122, "inventory_aging", "Inventory aging", "inventory", "free"),
    ReportDef(123, "dead_stock", "Dead stock", "inventory", "free"),
    ReportDef(130, "cash_flow", "Cash flow", "cash_flow", "free", requires_gl=True),
    ReportDef(131, "outstanding_receivables", "Outstanding receivables / aging", "outstanding", "free"),
    ReportDef(132, "outstanding_payables", "Outstanding payables", "outstanding", "free"),
    ReportDef(140, "trial_balance", "Trial balance", "financial_statements", "free", requires_gl=True),
    ReportDef(142, "balance_sheet", "Balance sheet", "financial_statements", "free", requires_gl=True),
    ReportDef(150, "cashier_wise_sales", "Cashier-wise sales", "pos", "free"),
    ReportDef(151, "shift_report", "Shift report", "pos", "free"),
    ReportDef(152, "cash_drawer", "Cash drawer", "pos", "free"),
    ReportDef(153, "pos_returns", "POS returns", "pos", "free"),
    ReportDef(160, "admin_audit", "Admin / security audit", "admin_security", "free"),
    ReportDef(161, "sink_sync_status", "Accounting sink sync status", "admin_security", "free"),
]

# Single paid teaser for demo/free accounts
PAID_TEASERS: list[ReportDef] = [
    ReportDef(
        141,
        "profit_and_loss",
        "Profit & loss",
        "financial_statements",
        "paid",
        requires_gl=True,
        description="Upgrade to unlock full P&L from Zoho/Tally.",
    ),
]

REGISTRY: dict[str, ReportDef] = {r.slug: r for r in FREE_REPORTS + PAID_TEASERS}
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
