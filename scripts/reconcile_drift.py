"""Flag fin_transactions whose header does not match line totals (ops / cron)."""

from __future__ import annotations

import sys
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sqlalchemy import text

from db.reconcile import LINE_RECONCILE_TOLERANCE
from db.session import get_engine


def find_drifted_rows(tolerance: Decimal = LINE_RECONCILE_TOLERANCE) -> list[dict]:
    engine = get_engine()
    sql = text(
        """
        SELECT t.id::text AS id,
               t.tenant_id::text AS tenant_id,
               t.source_system,
               t.source_ref,
               t.amount,
               t.tax_amount,
               COALESCE(SUM(l.line_amount), 0) AS sum_line_amount,
               COALESCE(SUM(l.line_tax), 0) AS sum_line_tax
        FROM fin_transactions t
        LEFT JOIN fin_transaction_lines l ON l.transaction_id = t.id
        GROUP BY t.id, t.tenant_id, t.source_system, t.source_ref, t.amount, t.tax_amount
        HAVING ABS(t.amount - (COALESCE(SUM(l.line_amount), 0) + COALESCE(SUM(l.line_tax), 0)))
               > :tol
           OR ABS(t.tax_amount - COALESCE(SUM(l.line_tax), 0)) > :tol
        """
    )
    with engine.connect() as conn:
        rows = conn.execute(sql, {"tol": float(tolerance)}).mappings().all()
    return [dict(r) for r in rows]


if __name__ == "__main__":
    drifted = find_drifted_rows()
    print(f"drifted_rows={len(drifted)}")
    for r in drifted[:50]:
        print(r)
    sys.exit(1 if drifted else 0)
