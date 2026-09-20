from dataclasses import dataclass, field
from decimal import Decimal
from typing import Optional


class ScrapeError(Exception):
    """Something went wrong. `retryable` decides whether we try again."""

    def __init__(self, message, retryable=True, status=None):
        super().__init__(message)
        self.message = message
        self.retryable = retryable
        self.status = status


@dataclass
class ScrapeResult:
    price: Optional[Decimal] = None
    in_stock: Optional[bool] = None
    stock_text: str = ""
    name: str = ""
    currency: str = ""
    image_url: str = ""
    category: str = ""
    strategy: str = ""
    fingerprint: str = ""
    source_url: str = ""
    extra: dict = field(default_factory=dict)

    def is_usable(self):
        return self.price is not None
