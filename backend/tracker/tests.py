"""
Tests for the parts that decide whether a number gets stored.

These are the pieces worth testing: a bug in the parser or the validator would
put a wrong price into the history, which is exactly the failure mode the
assignment cares about. The strategies themselves are not tested here because
they depend on a live store.
"""

from decimal import Decimal

from django.test import TestCase

from tracker.models import PricePoint, Product
from tracker.scraper import parsing, validators


class PriceParsingTests(TestCase):
    def test_reads_common_formats(self):
        cases = {
            "₹1,299": Decimal("1299"),
            "$1,234.56": Decimal("1234.56"),
            "1.234,56 EUR": Decimal("1234.56"),
            "Rs. 499": Decimal("499"),
            "₹ 89.99": Decimal("89.99"),
            "9,99 €": Decimal("9.99"),
        }
        for text, expected in cases.items():
            self.assertEqual(parsing.parse_price(text), expected, msg=text)

    def test_picks_sale_price_not_struck_through_price(self):
        self.assertEqual(parsing.parse_price("₹2,499 ₹3,999"), Decimal("2499"))

    def test_returns_none_rather_than_guessing(self):
        for text in ["", None, "abc", "Price unavailable", "0"]:
            self.assertIsNone(parsing.parse_price(text), msg=repr(text))


class StockParsingTests(TestCase):
    def test_out_of_stock_beats_the_word_stock(self):
        self.assertEqual(parsing.parse_stock("Out of Stock")[0], False)

    def test_counts(self):
        self.assertEqual(parsing.parse_stock("12 left")[0], True)

    def test_unknown_stays_unknown(self):
        self.assertIsNone(parsing.parse_stock("shipping info")[0])


class ValidationTests(TestCase):
    def setUp(self):
        self.product = Product.objects.create(
            store_product_id="test-1", name="Test widget",
            url="https://example.invalid/p/1",
        )

    def _result(self, price):
        from tracker.scraper.result import ScrapeResult

        return ScrapeResult(price=Decimal(str(price)))

    def test_rejects_empty_and_negative(self):
        self.assertFalse(validators.validate(self.product, self._result(0)))
        self.assertFalse(validators.validate(self.product, None))

    def test_accepts_a_normal_price(self):
        self.assertTrue(validators.validate(self.product, self._result(1000)))

    def test_flags_a_wild_outlier_for_confirmation(self):
        for p in ["1000", "1010", "995", "1005"]:
            PricePoint.objects.create(product=self.product, price=Decimal(p))
        verdict = validators.validate(self.product, self._result(19))
        self.assertFalse(verdict.ok)
        self.assertTrue(verdict.needs_confirmation)

    def test_small_movement_is_not_an_outlier(self):
        for p in ["1000", "1010", "995", "1005"]:
            PricePoint.objects.create(product=self.product, price=Decimal(p))
        self.assertTrue(validators.validate(self.product, self._result(1100)))
