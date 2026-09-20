"""
Turning messy page text into a number we are willing to store.

The rule everywhere in this module: when the text is ambiguous, return None.
An honest "I could not read this" is recorded as a failure; a guess would
silently poison the price history.
"""

import re
from decimal import Decimal, InvalidOperation

from . import config

CURRENCY_SYMBOLS = "₹$€£¥"
_CURRENCY_RE = re.compile(f"[{CURRENCY_SYMBOLS}]|INR|USD|EUR|GBP|Rs\\.?", re.I)

# A number with optional thousands separators and optional decimals.
_NUMBER_RE = re.compile(r"\d{1,3}(?:[.,]\d{3})*(?:[.,]\d{1,2})?|\d+(?:[.,]\d{1,2})?")

_STRIKETHROUGH_HINTS = ("was", "mrp", "list price", "compare at", "original", "rrp")


def detect_currency(text: str) -> str:
    if not text:
        return ""
    m = _CURRENCY_RE.search(text)
    return m.group(0) if m else ""


def _normalise_number(raw: str):
    """
    '1,234.56' -> 1234.56      '1.234,56' -> 1234.56
    '1,234'    -> 1234         '1234'     -> 1234
    """
    raw = raw.strip()
    has_comma, has_dot = "," in raw, "." in raw

    if has_comma and has_dot:
        # whichever separator comes last is the decimal point
        dec = "," if raw.rfind(",") > raw.rfind(".") else "."
        thou = "." if dec == "," else ","
        raw = raw.replace(thou, "").replace(dec, ".")
    elif has_comma:
        # ',' is decimal only when it's followed by exactly 1-2 digits at the end
        raw = (
            raw.replace(",", ".")
            if re.fullmatch(r"\d+,\d{1,2}", raw)
            else raw.replace(",", "")
        )
    elif has_dot:
        if not re.fullmatch(r"\d+\.\d{1,2}", raw):
            raw = raw.replace(".", "")

    try:
        return Decimal(raw)
    except InvalidOperation:
        return None


def parse_price(text):
    """
    Extract a price from arbitrary text.

    If the text holds several numbers (e.g. "₹1,299  ₹1,899" for a sale price
    next to the crossed-out original) we take the *lowest plausible* one, which
    is the price actually being charged.
    """
    if text is None:
        return None
    text = str(text).strip()
    if not text:
        return None

    lowered = text.lower()
    if any(h in lowered for h in _STRIKETHROUGH_HINTS) and len(text) > 40:
        # A blob mixing "MRP" with the live price is too ambiguous to trust.
        return None

    candidates = []
    for m in _NUMBER_RE.finditer(text):
        value = _normalise_number(m.group(0))
        if value is None:
            continue
        f = float(value)
        if config.MIN_PRICE <= f <= config.MAX_PRICE:
            candidates.append(value)

    if not candidates:
        return None
    return min(candidates)


def parse_stock(text):
    """
    Returns (in_stock: bool|None, cleaned_text: str).
    None means "the page did not tell us", which is not the same as False.
    """
    if not text:
        return None, ""
    cleaned = " ".join(str(text).split())[:120]
    lowered = cleaned.lower()

    # check "out of stock" first: it contains the substring "stock"
    for word in config.OUT_OF_STOCK_WORDS:
        if word in lowered:
            return False, cleaned
    for word in config.IN_STOCK_WORDS:
        if word in lowered:
            return True, cleaned

    # "12 left", "3 units remaining"
    m = re.search(r"(\d+)\s*(left|remaining|in stock|units|available)", lowered)
    if m:
        return int(m.group(1)) > 0, cleaned

    if re.fullmatch(r"\d+", lowered):
        return int(lowered) > 0, cleaned

    return None, cleaned


def first_text(soup_or_el, selectors):
    """Return text from the first selector that matches and is non-empty."""
    for sel in selectors:
        for el in soup_or_el.select(sel):
            # prefer an explicit data-* attribute over rendered text
            for attr in ("data-price", "data-stock", "data-name", "content", "value"):
                if el.has_attr(attr) and str(el[attr]).strip():
                    return str(el[attr]).strip(), sel
            txt = el.get_text(" ", strip=True)
            if txt:
                return txt, sel
    return None, None


def slugify_id(value: str) -> str:
    return re.sub(r"[^a-z0-9\-]+", "-", str(value).lower()).strip("-")
