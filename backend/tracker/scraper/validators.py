"""
The gate between "we read something" and "we wrote it down".

The assignment's hardest requirement is *never store wrong or empty data on
failure*. That is enforced here, not in the strategies — a strategy is allowed
to be optimistic, but nothing reaches the price history without passing this.
"""

import statistics
from decimal import Decimal

from . import config


class Verdict:
    def __init__(self, ok, reason="", needs_confirmation=False):
        self.ok = ok
        self.reason = reason
        self.needs_confirmation = needs_confirmation

    def __bool__(self):
        return self.ok


def rolling_median(product, window=9):
    values = list(
        product.price_points.order_by("-scraped_at").values_list("price", flat=True)[:window]
    )
    if not values:
        return None
    return statistics.median(float(v) for v in values)


def validate(product, result):
    if result is None or result.price is None:
        return Verdict(False, "no price extracted")

    price = Decimal(result.price)
    if price <= 0:
        return Verdict(False, f"non-positive price {price}")
    if not (Decimal(str(config.MIN_PRICE)) <= price <= Decimal(str(config.MAX_PRICE))):
        return Verdict(False, f"price {price} outside plausible range")

    history_count = product.price_points.count()
    if history_count >= config.ANOMALY_MIN_HISTORY:
        median = rolling_median(product)
        if median and median > 0:
            drift = abs(float(price) - median) / median
            if drift > config.ANOMALY_RATIO:
                return Verdict(
                    False,
                    f"price {price} is {drift:.0%} away from rolling median {median:.2f}",
                    needs_confirmation=True,
                )
    return Verdict(True)


def confirmation_matches(first, second, tolerance=Decimal("0.01")):
    """Two independent reads agree → the outlier is real, not a parse error."""
    if second is None or second.price is None:
        return False
    return abs(Decimal(first.price) - Decimal(second.price)) <= tolerance
