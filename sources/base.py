"""Source adapter contract — all sources implement translate(raw) -> NormalizedTransaction."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any, Optional, Protocol
from uuid import UUID


@dataclass
class NormalizedLine:
    item_source_ref: str
    item_name: str
    qty: Decimal
    rate: Decimal
    line_amount: Decimal
    line_tax: Decimal = Decimal("0")
    hsn_sac: Optional[str] = None
    tax_rate: Optional[Decimal] = None


@dataclass
class NormalizedParty:
    source_ref: str
    name: str
    gstin: Optional[str] = None


@dataclass
class NormalizedTransaction:
    tenant_id: UUID
    source_system: str
    source_ref: str
    txn_date: date
    txn_type: str  # sale|purchase|payment|refund|stock_adjustment
    amount: Decimal
    tax_amount: Decimal = Decimal("0")
    tax_breakdown: dict[str, Any] = field(default_factory=dict)
    category: Optional[str] = None
    payment_mode: Optional[str] = None
    party: Optional[NormalizedParty] = None
    lines: list[NormalizedLine] = field(default_factory=list)


class SourceAdapter(Protocol):
    def translate(self, raw_event: dict[str, Any]) -> NormalizedTransaction:
        """Map a source-specific payload into the canonical shape. No posting logic."""
        ...
