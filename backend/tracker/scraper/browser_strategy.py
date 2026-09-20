"""
The browser strategy — the fallback, and the teacher.

This store hides its price behind a "Reveal price" control that only
activates after a genuine mouse hover, and the reveal can take a while to
resolve. Two jobs:

1. Render the page, do the hover/click interaction, and read the price that
   appears afterwards.
2. While rendering, listen to the page's own network traffic. Any JSON
   response that looks like product data gets saved to SiteProfile, so a
   future run can try plain HTTP first and skip the browser.

In headed mode it also injects a small on-page HUD so a screen recording
shows what the scraper is thinking.
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
_CURRENCY_RE = re.compile(r"[₹$€£¥]\s*[\d][\d.,]*")

# How long we're willing to wait for a revealed price to appear. The
# assignment explicitly says the store can be slow, so this is generous.
REVEAL_SETTLE_MS = getattr(config, "REVEAL_SETTLE_MS", 25_000)
REVEAL_ENABLE_TIMEOUT_MS = getattr(config, "REVEAL_ENABLE_TIMEOUT_MS", 6_000)

HUD_JS = """
(() => {
  if (window.__ineHud) return;
  const d = document.createElement('div');
  d.id = '__ine_hud';
  d.style.cssText = [
    'position:fixed','right:14px','top:14px','z-index:2147483647',
    'width:320px','padding:12px 14px','border-radius:10px',
    'background:rgba(14,18,28,.93)','color:#e8edf6',
    'font:12px/1.55 ui-monospace,SFMono-Regular,Menlo,monospace',
    'box-shadow:0 10px 30px rgba(0,0,0,.45)','white-space:pre-wrap',
    'max-height:70vh','overflow-y:auto'
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
            if "json" not in ctype or not _JSON_URL_HINT.search(url):
                return
            body = response.json()
            records = http_strategy.unwrap_list(body)
            if records:
                self.endpoints.append({"url": url, "count": len(records), "sample": records[0]})
        except Exception:
            return


# --------------------------------------------------------------------------
# Reveal interaction
# --------------------------------------------------------------------------
def _body_text(page):
    try:
        return page.evaluate("() => document.body ? document.body.innerText : ''") or ""
    except Exception:
        return ""


def _currency_matches(text):
    return set(m.group(0) for m in _CURRENCY_RE.finditer(text or ""))


def _find_reveal_control(page):
    """The clickable element. Text match first, then a few likely selectors."""
    try:
        by_text = page.get_by_text(re.compile(r"reveal\s*price", re.I))
        if by_text.count() > 0:
            return by_text.first
    except Exception:
        pass
    for sel in ["[data-testid*='reveal']", "button:has-text('Reveal')",
                ".reveal-price", "[data-action='reveal']"]:
        try:
            loc = page.locator(sel)
            if loc.count() > 0:
                return loc.first
        except Exception:
            continue
    return None


def _find_price_area(page):
    for sel in config.PRICE_SELECTORS:
        try:
            loc = page.locator(sel)
            for i in range(min(loc.count(), 3)):
                cand = loc.nth(i)
                if cand.is_visible():
                    return cand
        except Exception:
            continue
    return None


def _hover_dwell(page, box, emit):
    """A believable hover: move to the target, then jiggle a little and pause."""
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(cx - 6, cy - 4, steps=8)
    page.wait_for_timeout(150)
    page.mouse.move(cx, cy, steps=10)
    page.wait_for_timeout(250)
    page.mouse.move(cx + 3, cy + 2, steps=4)
    page.wait_for_timeout(600)
    emit("hovered over the price area")


def _reveal_live_price(page, headed=False, emit=None):
    """
    Hover the price area, then click 'Reveal price'. Returns True if a click
    was actually delivered — not whether a price showed up afterwards, that
    is checked separately by the caller.
    """
    def log_event(msg, tone="info"):
        if emit:
            emit(msg, tone)

    area = _find_price_area(page)
    if area is not None:
        try:
            box = area.bounding_box()
            if box:
                _hover_dwell(page, box, log_event)
        except Exception as exc:
            log_event(f"hover over price area failed: {exc}", "warn")

    reveal = _find_reveal_control(page)
    if reveal is None:
        log_event("no 'Reveal price' control found on the page", "warn")
        return False

    # Give the store's own JS a moment to react to the hover before we
    # check whether the button is enabled.
    page.wait_for_timeout(400)

    deadline = time.time() + REVEAL_ENABLE_TIMEOUT_MS / 1000.0
    enabled = False
    while time.time() < deadline:
        try:
            if reveal.is_visible() and reveal.is_enabled():
                enabled = True
                break
        except Exception:
            pass
        # keep the hover alive while we wait — some stores disarm on idle
        try:
            box = reveal.bounding_box()
            if box:
                page.mouse.move(box["x"] + box["width"] / 2,
                                box["y"] + box["height"] / 2, steps=3)
        except Exception:
            pass
        page.wait_for_timeout(300)

    if not enabled:
        log_event("Reveal price control never became enabled — clicking anyway", "warn")

    try:
        log_event("clicking reveal price")
        if headed:
            _hud(page, "clicking Reveal price…")
        reveal.click(timeout=5000, force=not enabled)
        log_event("reveal clicked", "good")
        return True
    except Exception as exc:
        log_event(f"reveal click failed: {exc}", "warn")
        return False


def _wait_for_revealed_price(page, before_matches, headed=False, emit=None):
    """
    Poll for a currency amount that was NOT already visible before the click.
    This sidesteps brittle selectors entirely: we don't care where in the
    DOM the price lives, only that new price-shaped text appeared and then
    held steady for half a second.
    """
    def log_event(msg, tone="info"):
        if emit:
            emit(msg, tone)

    deadline = time.time() + REVEAL_SETTLE_MS / 1000.0
    last_candidate, stable_since = None, None
    checked_networkidle = False

    while time.time() < deadline:
        if not checked_networkidle and time.time() > deadline - REVEAL_SETTLE_MS / 1000.0 + 2:
            try:
                page.wait_for_load_state("networkidle", timeout=3000)
            except Exception:
                pass
            checked_networkidle = True

        now_matches = _currency_matches(_body_text(page))
        new_matches = now_matches - before_matches
        candidate = sorted(new_matches, key=len, reverse=True)[0] if new_matches else None

        if candidate:
            if candidate == last_candidate and stable_since and (time.time() - stable_since) > 0.5:
                log_event(f"revealed price settled: {candidate!r}", "good")
                return candidate
            if candidate != last_candidate:
                last_candidate, stable_since = candidate, time.time()
                log_event(f"new price-shaped text appeared: {candidate!r}, confirming…")
        page.wait_for_timeout(500)

    # last resort: any currency text at all, even if it was already present
    # before the click (covers stores that show a masked price like
    # "₹•••" beforehand and a real one after — our regex requires digits so
    # the masked version would not have matched `before_matches` anyway).
    if last_candidate:
        log_event(f"accepting unsettled candidate {last_candidate!r} at timeout", "warn")
        return last_candidate

    raise ScrapeError("no revealed price appeared within the settle window", retryable=True)


# --------------------------------------------------------------------------
# Main entry point
# --------------------------------------------------------------------------
def scrape_via_browser(product, headed=False, slow_mo=0, learn=None, url=None, on_event=None):
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
                headless=not headed, slow_mo=slow_mo,
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
                page.goto(url, wait_until="domcontentloaded", timeout=config.BROWSER_TIMEOUT_MS)
            except PWTimeout as exc:
                raise ScrapeError("navigation timed out", retryable=True) from exc

            if headed:
                _hud(page, "page loaded")
            emit("page loaded, waiting for late content")

            try:
                page.wait_for_load_state("networkidle", timeout=8000)
            except PWTimeout:
                emit("network never went idle — continuing anyway", "warn")

            page.wait_for_timeout(1200)  # let React / challenge JS settle

            before_matches = _currency_matches(_body_text(page))
            emit(f"price-shaped text visible before reveal: {sorted(before_matches) or 'none'}")

            clicked = _reveal_live_price(page, headed=headed, emit=emit)
            if not clicked:
                emit("could not click a reveal control; watching for a late price anyway", "warn")

            if headed:
                _hud(page, "waiting for the live price to settle…")
            emit("waiting for the revealed price to settle")

            price_text = _wait_for_revealed_price(page, before_matches, headed=headed, emit=emit)
            price = parsing.parse_price(price_text)
            if price is None:
                raise ScrapeError(f"unreadable price text: {price_text!r}", retryable=True)

            stock_text = page.evaluate(
                """(sel) => {
                    for (const s of sel.split(',')) {
                        const el = document.querySelector(s.trim());
                        if (!el) continue;
                        const v = el.getAttribute('data-stock') || el.textContent.trim();
                        if (v) return v;
                    }
                    return null;
                }""",
                ",".join(config.STOCK_SELECTORS),
            ) or _body_text(page)[:3000]
            in_stock, stock_clean = parsing.parse_stock(stock_text)

            name_text = page.evaluate(
                """(sel) => {
                    for (const s of sel.split(',')) {
                        const el = document.querySelector(s.trim());
                        if (el && el.textContent.trim()) return el.textContent.trim();
                    }
                    return null;
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
                price=price, in_stock=in_stock, stock_text=stock_clean,
                name=str(name_text or "").strip(),
                currency=parsing.detect_currency(price_text),
                strategy="browser",
                fingerprint=fingerprint.fingerprint_soup(soup),
                source_url=url,
                extra={
                    "price_text": price_text,
                    "reveal_clicked": clicked,
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
    Open the store front page, let it load its own data, and return
    (records, endpoint_url). Used to seed SiteProfile.
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
                card.get("data-product-id") or card.get("data-id")
                or (href.rstrip("/").split("/")[-1] if href else "")
                or parsing.slugify_id(name_text)
            )
            img = card.find("img")
            out.append({
                "id": str(pid), "name": name_text.strip(), "price": price_text or "",
                "url": href or f"{config.STORE_BASE_URL}/product/{pid}",
                "image": (img.get("src") if img else "") or "",
            })
        if out:
            break
    return out


def dump_debug_html(path, headed=False):
    """Used by `manage.py probe_store` — dumps the DOM before any reveal."""
    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed, args=["--no-sandbox"])
        page = browser.new_context(user_agent=config.USER_AGENT).new_page()
        seen = []
        page.on("response", lambda r: seen.append(
            (r.url, r.status, r.header_value("content-type") or "")))
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
            json.dump([{"url": u, "status": s, "type": c} for u, s, c in seen], fh, indent=2)
        browser.close()
    return html, seen


def dump_debug_reveal(path, product_url, headed=False):
    """
    Ground-truth capture: navigate to one product, do the exact hover+click
    the scraper does, then dump the HTML *after* the reveal plus every
    network response seen, so you can see precisely what changed.
    """
    from playwright.sync_api import sync_playwright

    events = []

    def emit(msg, tone="info"):
        events.append(f"[{tone}] {msg}")

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=not headed, args=["--no-sandbox"])
        page = browser.new_context(
            user_agent=config.USER_AGENT, viewport={"width": 1280, "height": 860}
        ).new_page()
        seen = []
        page.on("response", lambda r: seen.append(
            (r.url, r.status, r.header_value("content-type") or "")))
        if headed:
            _install_hud(page)

        page.goto(product_url, wait_until="domcontentloaded", timeout=45000)
        try:
            page.wait_for_load_state("networkidle", timeout=8000)
        except Exception:
            pass
        page.wait_for_timeout(1200)

        before_html = page.content()
        before_matches = _currency_matches(_body_text(page))

        clicked = _reveal_live_price(page, headed=headed, emit=emit)
        try:
            price_text = _wait_for_revealed_price(page, before_matches, headed=headed, emit=emit)
        except ScrapeError as exc:
            price_text = f"<<not found: {exc.message}>>"

        page.wait_for_timeout(1000)
        after_html = page.content()

        with open(path + ".before.html", "w", encoding="utf-8") as fh:
            fh.write(before_html)
        with open(path + ".after.html", "w", encoding="utf-8") as fh:
            fh.write(after_html)
        with open(path + ".network.json", "w", encoding="utf-8") as fh:
            json.dump([{"url": u, "status": s, "type": c} for u, s, c in seen], fh, indent=2)
        with open(path + ".events.txt", "w", encoding="utf-8") as fh:
            fh.write(f"reveal clicked: {clicked}\nfinal price text: {price_text}\n\n")
            fh.write("\n".join(events))

        browser.close()

    return price_text, events, seen