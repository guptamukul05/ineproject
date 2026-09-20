"""
The browser strategy — the fallback, and the teacher.

Two jobs:

1. Render the JavaScript store when nothing lighter can read it.
2. While rendering, listen to the page's own network traffic. Any JSON
   response that looks like product data gets saved to SiteProfile, so the
   *next* run can use plain HTTP and skip the browser entirely.

In headed mode it also injects a small on-page HUD so a screen recording
shows what the scraper is thinking: which attempt it is on, what it is
waiting for, and what it finally read.
"""

import json
import logging
import re
import time

from bs4 import BeautifulSoup

from . import config, fingerprint, http_strategy, parsing
from .result import ScrapeError, ScrapeResult

log = logging.getLogger("tracker.scraper.browser")

_JSON_URL_HINT = re.compile(r"(product|item|catalog|price|stock|graphql)", re.I)

HUD_JS = """
(() => {
  if (window.__ineHud) return;
  const d = document.createElement('div');
  d.id = '__ine_hud';
  d.style.cssText = [
    'position:fixed','right:14px','top:14px','z-index:2147483647',
    'width:310px','padding:12px 14px','border-radius:10px',
    'background:rgba(14,18,28,.93)','color:#e8edf6',
    'font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace',
    'box-shadow:0 10px 30px rgba(0,0,0,.45)','white-space:pre-wrap'
  ].join(';');
  document.documentElement.appendChild(d);
  window.__ineHud = (line, tone) => {
    const colour = tone === 'bad' ? '#ff9c8a' : tone === 'good' ? '#8ce7b0' : '#9fb4d6';
    const t = new Date().toLocaleTimeString();
    d.innerHTML += `<div style="color:${colour}">${t}  ${line}</div>`;
    d.scrollTop = d.scrollHeight;
  };
  window.__ineHud('scraper attached', 'good');
})();
"""


def _hud(page, line, tone="info"):
    try:
        page.evaluate("([l,t]) => window.__ineHud && window.__ineHud(l,t)", [line, tone])
    except Exception:
        pass


def _install_hud(page):
    try:
        page.add_init_script(HUD_JS)
    except Exception:
        pass


class LearnedTraffic:
    """Collects JSON responses the page fetched for itself."""

    def __init__(self):
        self.endpoints = []

    def handle(self, response):
        try:
            url = response.url
            ctype = (response.header_value("content-type") or "").lower()
            if "json" not in ctype:
                return
            if not _JSON_URL_HINT.search(url):
                return
            body = response.json()
            records = http_strategy.unwrap_list(body)
            if records:
                self.endpoints.append({"url": url, "count": len(records), "sample": records[0]})
        except Exception:
            return


def _wait_for_price(page, timeout_ms):
    """
    Wait until *a price is actually readable*, not merely until the network
    goes quiet. The store loads some content late, so 'networkidle' alone is
    not proof the number on screen is final.
    """
    deadline = time.time() + timeout_ms / 1000.0
    selectors = ",".join(config.PRICE_SELECTORS)
    last_seen, stable_since = None, None

    while time.time() < deadline:
        try:
            text = page.evaluate(
                """(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return null;
                    return el.getAttribute('data-price') || el.textContent.trim();
                }""",
                selectors,
            )
        except Exception:
            text = None

        if text is None:
            body = page.evaluate("() => document.body ? document.body.innerText : ''")
            m = re.search(r"[₹$€£¥]\s*[\d.,]+", body or "")
            text = m.group(0) if m else None

        if text and parsing.parse_price(text) is not None:
            if text == last_seen:
                # seen the same value twice ~600ms apart -> treat as settled
                if stable_since and (time.time() - stable_since) > 0.5:
                    return text
            else:
                last_seen, stable_since = text, time.time()
        page.wait_for_timeout(400)

    if last_seen:
        return last_seen
    raise ScrapeError("price never appeared within settle window", retryable=True)


def scrape_via_browser(product, headed=False, slow_mo=0, learn=None, url=None,
                       on_event=None):
    try:
        from playwright.sync_api import Error as PWError
        from playwright.sync_api import TimeoutError as PWTimeout
        from playwright.sync_api import sync_playwright
    except ImportError as exc:  # pragma: no cover
        raise ScrapeError("playwright is not installed", retryable=False) from exc

    url = url or product.url
    traffic = LearnedTraffic()

    def emit(msg, tone="info"):
        log.info("[browser] %s", msg)
        if on_event:
            on_event(msg, tone)

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(
                headless=not headed,
                slow_mo=slow_mo,
                args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
            )
        except PWError as exc:
            raise ScrapeError(f"could not launch chromium: {exc}", retryable=False) from exc

        ctx = browser.new_context(
            user_agent=config.USER_AGENT,
            viewport={"width": 1280, "height": 860},
            locale="en-US",
        )
        page = ctx.new_page()
        page.on("response", traffic.handle)
        if headed:
            _install_hud(page)

        from . import chaos

        if chaos.is_enabled():
            chaos.install_route(page)
            emit("fault injection is active for this run", "warn")

        try:
            emit(f"navigating to {url}")
            try:
                page.goto(url, wait_until="domcontentloaded",
                          timeout=config.BROWSER_TIMEOUT_MS)
            except PWTimeout as exc:
                raise ScrapeError("navigation timed out", retryable=True) from exc

            if headed:
                _hud(page, "waiting for price to settle…")
            emit("page loaded, waiting for late content")

            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except PWTimeout:
                emit("network never went idle — continuing anyway", "warn")
                if headed:
                    _hud(page, "network still busy, reading anyway", "bad")

            price_text = _wait_for_price(page, config.CONTENT_SETTLE_MS)
            price = parsing.parse_price(price_text)
            if price is None:
                raise ScrapeError(f"unreadable price text: {price_text!r}", retryable=True)

            stock_text = page.evaluate(
                """(sel) => {
                    const el = document.querySelector(sel);
                    if (!el) return null;
                    return el.getAttribute('data-stock') || el.textContent.trim();
                }""",
                ",".join(config.STOCK_SELECTORS),
            )
            if not stock_text:
                stock_text = (page.evaluate("() => document.body.innerText") or "")[:3000]
            in_stock, stock_clean = parsing.parse_stock(stock_text)

            name_text = page.evaluate(
                """(sel) => {
                    const el = document.querySelector(sel);
                    return el ? el.textContent.trim() : null;
                }""",
                ",".join(config.NAME_SELECTORS),
            ) or product.name

            html = page.content()
            soup = BeautifulSoup(html, "lxml")

            if headed:
                _hud(page, f"read price={price}  stock={stock_clean or '?'}", "good")
                page.wait_for_timeout(1500)

            emit(f"extracted price={price} in_stock={in_stock}", "good")

            if learn is not None and traffic.endpoints:
                learn.extend(traffic.endpoints)

            return ScrapeResult(
                price=price,
                in_stock=in_stock,
                stock_text=stock_clean,
                name=str(name_text or "").strip(),
                currency=parsing.detect_currency(price_text),
                strategy="browser",
                fingerprint=fingerprint.fingerprint_soup(soup),
                source_url=url,
                extra={
                    "price_text": price_text,
                    "learned_endpoints": [e["url"] for e in traffic.endpoints],
                },
            )
        finally:
            try:
                ctx.close()
                browser.close()
            except Exception:
                pass


def discover_catalogue(headed=False, limit=200):
    """
    Open the store front page in a browser, let it load its own data, and
    return (records, endpoint_url). Used to seed SiteProfile.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise ScrapeError("playwright is not installed", retryable=False) from exc

    traffic = LearnedTraffic()
    records, endpoint = [], ""

    with sync_playwright() as pw:
        browser = pw.chromium.launch(
            headless=not headed,
            args=["--no-sandbox", "--disable-dev-shm-usage", "--disable-gpu"],
        )
        ctx = browser.new_context(user_agent=config.USER_AGENT,
                                  viewport={"width": 1440, "height": 900})
        page = ctx.new_page()
        page.on("response", traffic.handle)
        try:
            page.goto(config.STORE_BASE_URL, wait_until="domcontentloaded",
                      timeout=config.BROWSER_TIMEOUT_MS)
            try:
                page.wait_for_load_state("networkidle", timeout=12000)
            except Exception:
                pass
            page.wait_for_timeout(2500)

            # scroll once, in case the catalogue lazy-loads
            page.evaluate("() => window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(1800)

            if traffic.endpoints:
                best = max(traffic.endpoints, key=lambda e: e["count"])
                endpoint = best["url"]
                payload, _ = http_strategy.fetch(endpoint, expect_json=True)
                records = http_strategy.unwrap_list(payload)[:limit]
            else:
                records = _cards_from_dom(page)[:limit]
        finally:
            ctx.close()
            browser.close()

    return records, endpoint


def _cards_from_dom(page):
    """Fallback catalogue read: scrape the rendered listing grid."""
    html = page.content()
    soup = BeautifulSoup(html, "lxml")
    out = []
    for sel in config.CARD_SELECTORS:
        cards = soup.select(sel)
        if len(cards) < 2:
            continue
        for card in cards:
            name_text, _ = parsing.first_text(card, config.NAME_SELECTORS + ["a", "h2", "h3"])
            price_text, _ = parsing.first_text(card, config.PRICE_SELECTORS)
            if not name_text:
                continue
            link = card.find("a", href=True)
            href = link["href"] if link else ""
            if href.startswith("/"):
                href = config.STORE_BASE_URL + href
            pid = (
                card.get("data-product-id")
                or card.get("data-id")
                or (href.rstrip("/").split("/")[-1] if href else "")
                or parsing.slugify_id(name_text)
            )
            img = card.find("img")
            out.append(
                {
                    "id": str(pid),
                    "name": name_text.strip(),
                    "price": price_text or "",
                    "url": href or f"{config.STORE_BASE_URL}/product/{pid}",
                    "image": (img.get("src") if img else "") or "",
                }
            )
        if out:
            break
    return out


def dump_debug_html(path, headed=False):
    """Used by `manage.py probe_store` so you can eyeball the rendered DOM."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed, args=["--no-sandbox"])
        page = browser.new_context(user_agent=config.USER_AGENT).new_page()
        seen = []
        page.on("response", lambda r: seen.append((r.url, r.status,
                                                   r.header_value("content-type") or "")))
        page.goto(config.STORE_BASE_URL, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=12000)
        except Exception:
            pass
        page.wait_for_timeout(2500)
        html = page.content()
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(html)
        with open(path + ".network.json", "w", encoding="utf-8") as fh:
            json.dump(
                [{"url": u, "status": s, "type": c} for u, s, c in seen], fh, indent=2
            )
        browser.close()
    return html, seen
