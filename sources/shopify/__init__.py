"""Source adapter contract stubs — implement later; Autom8 is the only live source."""

from __future__ import annotations

from typing import Any

from sources.base import NormalizedTransaction, SourceAdapter


class NotImplementedSource:
    """Documented stub — raise until a real adapter is shipped."""

    source_system: str = "generic"

    def translate(self, raw_event: dict[str, Any]) -> NormalizedTransaction:
        raise NotImplementedError(
            f"{self.source_system} source adapter is not implemented in this phase. "
            "Contract: translate(raw_event) -> NormalizedTransaction "
            "(same fields as sources.base.NormalizedTransaction)."
        )


class ShopifySourceAdapter(NotImplementedSource):
    source_system = "shopify"


class WooCommerceSourceAdapter(NotImplementedSource):
    source_system = "woocommerce"


class CsvUploadSourceAdapter(NotImplementedSource):
    source_system = "csv_upload"


class GenericWebhookSourceAdapter(NotImplementedSource):
    source_system = "generic_webhook"


__all__ = [
    "SourceAdapter",
    "ShopifySourceAdapter",
    "WooCommerceSourceAdapter",
    "CsvUploadSourceAdapter",
    "GenericWebhookSourceAdapter",
]
