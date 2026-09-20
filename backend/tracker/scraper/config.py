"""
Tunables for the scraper.

Everything the store *might* call things lives here as a candidate list rather
than a single hard-coded selector. The store is described as "deliberately
awkward", so the scraper is written to survive a rename of any one class.
"""

from django.conf import settings

STORE_BASE_URL = settings.STORE_BASE_URL.rstrip("/")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
)

# --- retry policy -----------------------------------------------------------
MAX_ATTEMPTS = 4
BACKOFF_SECONDS = [2, 5, 11]      # waits *between* attempts 1->2, 2->3, 3->4
BACKOFF_JITTER = 0.4              # +/- 40% random jitter so retries desynchronise
HTTP_TIMEOUT = 20                 # seconds per HTTP request
BROWSER_TIMEOUT_MS = 45_000       # hard cap on a browser attempt
CONTENT_SETTLE_MS = 12_000        # how long to wait for late-loading price text
RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504, 520, 522, 524}

# --- validation -------------------------------------------------------------
MIN_PRICE = 0.01
MAX_PRICE = 10_000_000
# If a new price differs from the rolling median by more than this, we do not
# trust a single reading — we re-fetch with a *different* strategy to confirm.
ANOMALY_RATIO = 0.40
ANOMALY_MIN_HISTORY = 3

# --- where prices/stock/names tend to live ---------------------------------
PRICE_SELECTORS = [
    "[data-price]",
    "[data-testid*='price']",
    "[id*='price']",
    ".product-price",
    ".price-value",
    ".price-now",
    ".current-price",
    ".price",
    "span.amount",
    "[itemprop='price']",
]

STOCK_SELECTORS = [
    "[data-stock]",
    "[data-testid*='stock']",
    "[id*='stock']",
    ".stock-status",
    ".availability",
    ".product-stock",
    ".in-stock",
    ".out-of-stock",
    "[itemprop='availability']",
]

NAME_SELECTORS = [
    "[data-name]",
    "[data-testid*='title']",
    "h1",
    ".product-title",
    ".product-name",
    "[itemprop='name']",
]

CARD_SELECTORS = [
    "[data-product-id]",
    "[data-testid*='product-card']",
    ".product-card",
    ".product-item",
    ".product",
    "li.product",
    "article",
]

# Paths the SPA plausibly uses for its own data. Probed once, then cached in
# SiteProfile so we stop guessing on every run.
CANDIDATE_LIST_PATHS = [
    "/api/products",
    "/api/product",
    "/api/v1/products",
    "/products.json",
    "/api/items",
    "/data/products.json",
]

IN_STOCK_WORDS = [
    "in stock",
    "instock",
    "available",
    "add to cart",
    "buy now",
    "ready to ship",
]
OUT_OF_STOCK_WORDS = [
    "out of stock",
    "outofstock",
    "sold out",
    "unavailable",
    "back order",
    "backorder",
    "notify me",
]
