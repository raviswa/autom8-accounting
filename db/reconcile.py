from __future__ import annotations

from decimal import Decimal
from typing import NamedTuple

# Closest equivalent to "debits must equal credits" for this ledger.
LINE_RECONCILE_TOLERANCE = Decimal("0.02")


class ReconcileResult(NamedTuple):
    ok: bool
    header_total: Decimal
    lines_total: Decimal
    delta: Decimal


def reconcile_header_vs_lines(
    amount: Decimal,
    tax_amount: Decimal,
    line_amounts: list[Decimal],
    line_taxes: list[Decimal],
    *,
    tolerance: Decimal = LINE_RECONCILE_TOLERANCE,
) -> ReconcileResult:
    """
    Rule 3: fin_transactions.amount must equal
    SUM(line_amount) + SUM(line_tax) within tolerance.

    Note: `amount` is the grand total (incl. tax). `tax_amount` is informational;
    it must be consistent with sum(line_tax) within the same tolerance.
    """
    header_total = Decimal(amount).quantize(Decimal("0.01"))
    lines_ex_tax = sum((Decimal(x) for x in line_amounts), Decimal("0"))
    lines_tax = sum((Decimal(x) for x in line_taxes), Decimal("0"))
    lines_total = (lines_ex_tax + lines_tax).quantize(Decimal("0.01"))
    delta = abs(header_total - lines_total)
    tax_delta = abs(Decimal(tax_amount).quantize(Decimal("0.01")) - lines_tax.quantize(Decimal("0.01")))
    ok = delta <= tolerance and tax_delta <= tolerance
    return ReconcileResult(ok=ok, header_total=header_total, lines_total=lines_total, delta=delta)
