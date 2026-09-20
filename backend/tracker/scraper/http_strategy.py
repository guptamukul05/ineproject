"""
The two lightweight strategies: a cached JSON endpoint, and HTML parsing.

Both are plain `requests` — no browser, no event loop. These are tried before
the browser on every run, so the browser only starts when the page genuinely
needs rendering.
"""

import json
import logging
import re

import requests
from bs4 import BeautifulSoup

from . import config, fingerprint, parsing
from .result import ScrapeError, ScrapeResult

log = logging.getLogger("tracker.scraper.http")

_session = None


def session():
    global _session
    if _session is None:
        s = requests.Session()
        s.headers.update(
            {
                "User-Agent": config.USER_AGENT,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "Cache-Control": "no-cache",
            }
        )
        _session = s
    return _session


def fetch(url, expect_json=False, timeout=None):
    """One HTTP request. Raises ScrapeError, tagging what is worth retrying."""
    try:
        r = session().get(url, timeout=timeout or config.HTTP_TIMEOUT)
    except requests.Timeout as exc:
        raise ScrapeError(f"timeout after {config.HTTP_TIMEOUT}s", retryable=True) from exc
    except requests.RequestException as exc:
        raise ScrapeError(f"network error: {exc}", retryable=True) from exc

    if r.status_code in config.RETRYABLE_STATUS:
        raise ScrapeError(
            f"HTTP {r.status_code} (retryable)", retryable=True, status=r.status_code
        )
    if r.status_code == 404:
        raise ScrapeError("HTTP 404 — product page is gone", retryable=False, status=404)
    if r.status_code >= 400:
        raise ScrapeError(f"HTTP {r.status_code}", retryable=False, status=r.status_code)

    if expect_json:
        try:
            return r.json(), r
        except json.JSONDecodeError as exc:
            raise ScrapeError("endpoint did not return JSON", retryable=False) from exc
    return r.text, r


# --------------------------------------------------------------------------
# JSON field mapping
# --------------------------------------------------------------------------
_PRICE_KEYS = ["price", "current_price", "currentPrice", "sale_price", "salePrice",
               "amount", "unit_price", "value"]
_STOCK_KEYS = ["stock", "in_stock", "inStock", "availability", "available",
               "quantity", "qty", "stock_status", "stockStatus"]
_NAME_KEYS = ["name", "title", "product_name", "productName", "label"]
_ID_KEYS = ["id", "product_id", "productId", "sku", "slug", "code"]
_IMG_KEYS = ["image", "image_url", "imageUrl", "thumbnail", "img", "photo"]
_CAT_KEYS = ["category", "categoryName", "type", "department", "brand"]


def _pick(record, keys):
    for k in keys:
        if k in record and record[k] not in (None, ""):
            return record[k]
    # case-insensitive second pass
    lowered = {str(k).lower(): v for k, v in record.items()}
    for k in keys:
        v = lowered.get(k.lower())
        if v not in (None, ""):
            return v
    return None


def record_to_result(record, strategy="json_api"):
    if not isinstance(record, dict):
        raise ScrapeError("JSON record was not an object", retryable=False)

    raw_price = _pick(record, _PRICE_KEYS)
    price = parsing.parse_price(raw_price)

    raw_stock = _pick(record, _STOCK_KEYS)
    if isinstance(raw_stock, bool):
        in_stock, stock_text = raw_stock, "in stock" if raw_stock else "out of stock"
    elif isinstance(raw_stock, (int, float)):
        in_stock, stock_text = raw_stock > 0, f"{int(raw_stock)} in stock"
    else:
        in_stock, stock_text = parsing.parse_stock(raw_stock)

    return ScrapeResult(
        price=price,
        in_stock=in_stock,
        stock_text=stock_text,
        name=str(_pick(record, _NAME_KEYS) or ""),
        currency=parsing.detect_currency(str(raw_price)) or "",
        image_url=str(_pick(record, _IMG_KEYS) or ""),
        category=str(_pick(record, _CAT_KEYS) or ""),
        strategy=strategy,
        fingerprint=fingerprint.fingerprint_json(record),
        extra={"raw": {k: record[k] for k in list(record)[:25]}},
    )


def record_identity(record):
    return str(_pick(record, _ID_KEYS) or _pick(record, _NAME_KEYS) or "")


def unwrap_list(payload):
    """Find the list of product records inside an arbitrary JSON envelope."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for key in ("products", "items", "data", "results", "records", "rows"):
            v = payload.get(key)
            if isinstance(v, list):
                return v
            if isinstance(v, dict):
                inner = unwrap_list(v)
                if inner:
                    return inner
        # single product object
        if _pick(payload, _PRICE_KEYS) is not None:
            return [payload]
    return []


# --------------------------------------------------------------------------
# Strategy A: cached JSON endpoint
# --------------------------------------------------------------------------
def scrape_via_json(product, profile):
    if not profile or not profile.list_endpoint:
        raise ScrapeError("no JSON endpoint learned yet", retryable=False)

    if profile.detail_endpoint_template and "{id}" in profile.detail_endpoint_template:
        url = profile.detail_endpoint_template.format(id=product.store_product_id)
        payload, _ = fetch(url, expect_json=True)
        records = unwrap_list(payload) or ([payload] if isinstance(payload, dict) else [])
    else:
        url = profile.list_endpoint
        payload, _ = fetch(url, expect_json=True)
        records = unwrap_list(payload)

    if not records:
        raise ScrapeError("JSON endpoint returned no records", retryable=True)

    target = None
    for rec in records:
        if record_identity(rec) == product.store_product_id:
            target = rec
            break
    if target is None:
        for rec in records:
            if str(_pick(rec, _NAME_KEYS) or "").strip().lower() == product.name.strip().lower():
                target = rec
                break
    if target is None:
        raise ScrapeError("product not present in JSON response", retryable=True)

    res = record_to_result(target, "json_api")
    res.source_url = url
    if res.price is None:
        raise ScrapeError("JSON record had no readable price", retryable=True)
    return res


# --------------------------------------------------------------------------
# Strategy B: HTTP + HTML parse
# --------------------------------------------------------------------------
_INLINE_STATE_RE = re.compile(
    r"(?:__NEXT_DATA__|__NUXT__|__INITIAL_STATE__|window\.__DATA__)\s*=?\s*({.*?})\s*[;<]",
    re.S,
)


def _from_inline_state(html, product):
    """Some SPAs ship their data in a <script> blob. Cheaper than a browser."""
    for m in _INLINE_STATE_RE.finditer(html):
        try:
            payload = json.loads(m.group(1))
        except json.JSONDecodeError:
            continue
        for rec in unwrap_list(payload):
            if record_identity(rec) == product.store_product_id:
                return record_to_result(rec, "http_html")
    return None


def scrape_via_html(product, url=None):
    url = url or product.url
    html, resp = fetch(url)

    if len(html.strip()) < 400:
        raise ScrapeError(
            "page body is essentially empty — needs JavaScript rendering",
            retryable=False,
        )

    inline = _from_inline_state(html, product)
    if inline and inline.price is not None:
        inline.source_url = url
        return inline

    soup = BeautifulSoup(html, "lxml")

    price_text, price_sel = parsing.first_text(soup, config.PRICE_SELECTORS)
    price = parsing.parse_price(price_text)
    if price is None:
        # last resort: any element whose text looks like a currency amount
        for el in soup.find_all(string=re.compile(f"[{parsing.CURRENCY_SYMBOLS}]\\s*\\d")):
            price = parsing.parse_price(str(el))
            if price is not None:
                price_text, price_sel = str(el), "text-scan"
                break
    if price is None:
        raise ScrapeError("no price found in served HTML", retryable=False)

    stock_text, _ = parsing.first_text(soup, config.STOCK_SELECTORS)
    in_stock, stock_clean = parsing.parse_stock(stock_text or soup.get_text(" ", strip=True)[:3000])
    name_text, _ = parsing.first_text(soup, config.NAME_SELECTORS)

    return ScrapeResult(
        price=price,
        in_stock=in_stock,
        stock_text=stock_clean,
        name=(name_text or "").strip(),
        currency=parsing.detect_currency(price_text or ""),
        strategy="http_html",
        fingerprint=fingerprint.fingerprint_soup(soup),
        source_url=url,
        extra={"price_selector": price_sel, "http_status": resp.status_code},
    )


# --------------------------------------------------------------------------
# Catalogue search over HTTP (used by the search box)
# --------------------------------------------------------------------------
def list_products_via_json(profile):
    if not profile or not profile.list_endpoint:
        raise ScrapeError("no JSON endpoint learned yet", retryable=False)
    payload, _ = fetch(profile.list_endpoint, expect_json=True)
    records = unwrap_list(payload)
    if not records:
        raise ScrapeError("catalogue endpoint returned no records", retryable=True)
    return records


def probe_candidate_endpoints():
    """Blind-probe the usual API paths. Returns (url, records) or (None, [])."""
    for path in config.CANDIDATE_LIST_PATHS:
        url = f"{config.STORE_BASE_URL}{path}"
        try:
            payload, _ = fetch(url, expect_json=True, timeout=10)
        except ScrapeError:
            continue
        records = unwrap_list(payload)
        if records:
            log.info("discovered catalogue endpoint by probing: %s", url)
            return url, records
    return None, []
