"""Outbound sink contract: push(canonical tx) -> SyncResult. Append-only fin_sync_log."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Protocol
from uuid import UUID


@dataclass
class SyncResult:
    status: str  # success | failed | skipped
    external_id: Optional[str] = None
    error_detail: Optional[str] = None
    meta: dict[str, Any] = field(default_factory=dict)


class SinkAdapter(Protocol):
    system: str

    def push(self, *, tenant_id: UUID, transaction: Any, config: dict[str, Any]) -> SyncResult:
        """Map canonical transaction to remote accounting system. No GL posting locally."""
        ...
