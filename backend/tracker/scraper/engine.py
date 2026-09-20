"""
The engine. Everything else is a component; this is the thing that decides.

Per product, per run:

    for strategy in ladder:              # json_api -> http_html -> browser
        for attempt in 1..MAX_ATTEMPTS:  # backoff + jitter between attempts
            try strategy
            on retryable error: wait, retry
            on permanent error: break to next strategy
    validate
    if anomalous: confirm with a *different* strategy
    persist only if validated

Every branch writes a line into `trace`, so the scrape log can tell the truth
about slow responses, retries and failures rather than just "failed".
"""

import logging
import random
import time
from decimal import Decimal

from django.conf import settings
from django.utils import timezone

from ..models import Alert, PricePoint, Product, ScrapeAttempt, ScrapeRun, SiteProfile
from . import browser_strategy, config, http_strategy, validators
from .result import ScrapeError, ScrapeResult

log = logging.getLogger("tracker.scraper.engine")

LADDER = ["json_api", "http_html", "browser"]


# ---------------------------------------------------------------- site profile
def get_profile():
    profile, _ = SiteProfile.objects.get_or_create(key="default")
    return profile


def remember_endpoints(profile, learned):
    """Persist any JSON endpoint the browser saw the page use."""
    if not learned:
        return
    best = max(learned, key=lambda e: e.get("count", 0))
    if best["url"] and best["url"] != profile.list_endpoint:
        profile.list_endpoint = best["url"]
        profile.json_shape = {"keys": sorted(list(best.get("sample", {}).keys()))[:40]}
        profile.notes = (
            f"learned from browser traffic at {timezone.now():%Y-%m-%d %H:%M} UTC"
        )
        profile.save(update_fields=["list_endpoint", "json_shape", "notes", "updated_at"])
        log.info("learned catalogue endpoint: %s", best["url"])


def backoff_delay(attempt_index):
    base = config.BACKOFF_SECONDS[min(attempt_index, len(config.BACKOFF_SECONDS) - 1)]
    jitter = base * config.BACKOFF_JITTER
    return max(0.5, base + random.uniform(-jitter, jitter))


# ---------------------------------------------------------------- one strategy
def _run_strategy(name, product, profile, trace, headed=False, on_event=None,
                  learned=None):
    """
    Run one strategy with its own retry budget.
    Returns a ScrapeResult, or raises the last ScrapeError.
    """
    last_error = None
    for i in range(config.MAX_ATTEMPTS):
        started = time.time()
        try:
            if name == "json_api":
                res = http_strategy.scrape_via_json(product, profile)
            elif name == "http_html":
                res = http_strategy.scrape_via_html(product)
            elif name == "browser":
                if not settings.PLAYWRIGHT_ENABLED:
                    raise ScrapeError("browser strategy disabled by config",
                                      retryable=False)
                res = browser_strategy.scrape_via_browser(
                    product, headed=headed, slow_mo=350 if headed else 0,
                    learn=learned, on_event=on_event,
                )
            else:
                raise ScrapeError(f"unknown strategy {name}", retryable=False)

            trace.append(
                {
                    "strategy": name,
                    "try": i + 1,
                    "status": "ok",
                    "ms": int((time.time() - started) * 1000),
                    "price": str(res.price),
                }
            )
            return res, i + 1

        except ScrapeError as exc:
            last_error = exc
            trace.append(
                {
                    "strategy": name,
                    "try": i + 1,
                    "status": "error",
                    "ms": int((time.time() - started) * 1000),
                    "error": exc.message,
                    "retryable": exc.retryable,
                    "http_status": exc.status,
                }
            )
            if on_event:
                on_event(f"{name} try {i+1} failed: {exc.message}", "bad")
            if not exc.retryable or i == config.MAX_ATTEMPTS - 1:
                break
            delay = backoff_delay(i)
            trace.append({"strategy": name, "status": "backoff", "sleep_s": round(delay, 2)})
            time.sleep(delay)

        except Exception as exc:  # never let one product kill the whole run
            last_error = ScrapeError(f"unexpected: {exc.__class__.__name__}: {exc}",
                                     retryable=False)
            trace.append({"strategy": name, "try": i + 1, "status": "crash",
                          "error": str(exc)[:400]})
            break

    raise last_error or ScrapeError(f"{name} exhausted", retryable=False)


def _ordered_ladder(product):
    pref = product.preferred_strategy if product.preferred_strategy in LADDER else "json_api"
    return [pref] + [s for s in LADDER if s != pref]


# ---------------------------------------------------------------- one product
def scrape_product(product, run=None, headed=False, on_event=None):
    profile = get_profile()
    trace, learned = [], []
    started = time.time()
    result, tries_used, used_strategy = None, 0, ""
    errors = []

    for name in _ordered_ladder(product):
        try:
            result, tries_used = _run_strategy(
                name, product, profile, trace, headed=headed,
                on_event=on_event, learned=learned,
            )
            used_strategy = name
            break
        except ScrapeError as exc:
            errors.append(f"{name}: {exc.message}")
            continue

    remember_endpoints(profile, learned)

    duration_ms = int((time.time() - started) * 1000)
    total_tries = sum(1 for t in trace if t.get("status") in ("ok", "error", "crash"))

    # ---- failure: record honestly, store nothing ----
    if result is None:
        return _record_failure(product, run, trace, duration_ms, total_tries,
                               "; ".join(errors)[:1500], used_strategy)

    # ---- validation ----
    verdict = validators.validate(product, result)
    if not verdict.ok and verdict.needs_confirmation:
        if on_event:
            on_event(f"price looks anomalous ({verdict.reason}) — confirming", "warn")
        trace.append({"status": "anomaly", "reason": verdict.reason})
        confirm = _confirm_with_other_strategy(
            product, profile, used_strategy, trace, headed=headed, on_event=on_event
        )
        if validators.confirmation_matches(result, confirm):
            trace.append({"status": "anomaly_confirmed",
                          "note": "second independent read agreed"})
            verdict = validators.Verdict(True)
        else:
            trace.append({"status": "anomaly_rejected",
                          "note": "second read disagreed or failed"})
            attempt = ScrapeAttempt.objects.create(
                product=product, run=run, outcome="rejected",
                strategy=used_strategy, attempts=total_tries,
                duration_ms=duration_ms, price_found=result.price,
                in_stock_found=result.in_stock,
                error=f"rejected by validation: {verdict.reason}", trace=trace,
            )
            product.consecutive_failures += 1
            product.save(update_fields=["consecutive_failures"])
            _maybe_unhealthy_alert(product)
            return attempt

    if not verdict.ok:
        return _record_failure(product, run, trace, duration_ms, total_tries,
                               f"rejected by validation: {verdict.reason}",
                               used_strategy, outcome="rejected",
                               price=result.price)

    # ---- structure drift ----
    if result.fingerprint:
        if product.dom_fingerprint and product.dom_fingerprint != result.fingerprint:
            Alert.objects.create(
                product=product, kind="structure_change",
                message=(
                    f"Page shape changed for {product.name}. The scraper still read a "
                    f"valid price using the {used_strategy} strategy, but the selectors "
                    f"it matched are different from last time."
                ),
            )
            trace.append({"status": "fingerprint_changed",
                          "from": product.dom_fingerprint, "to": result.fingerprint})
        product.dom_fingerprint = result.fingerprint

    # ---- persist ----
    previous_price = product.last_price
    previous_stock = product.last_in_stock

    point = PricePoint.objects.create(
        product=product,
        price=Decimal(result.price),
        in_stock=result.in_stock,
        stock_text=result.stock_text[:120],
        strategy=used_strategy,
    )

    product.last_price = point.price
    product.last_in_stock = result.in_stock
    product.last_success_at = timezone.now()
    product.consecutive_failures = 0
    product.preferred_strategy = used_strategy
    if result.currency and not product.currency:
        product.currency = result.currency[:8]
    if result.image_url and not product.image_url:
        product.image_url = result.image_url[:600]
    if result.category and not product.category:
        product.category = result.category[:120]
    product.save()

    _emit_change_alerts(product, previous_price, previous_stock, point)

    retried = total_tries > 1 or len(errors) > 0
    attempt = ScrapeAttempt.objects.create(
        product=product, run=run,
        outcome="retried" if retried else "success",
        strategy=used_strategy, attempts=total_tries, duration_ms=duration_ms,
        price_found=point.price, in_stock_found=result.in_stock,
        error="; ".join(errors)[:1500] if errors else "", trace=trace,
    )
    return attempt


def _confirm_with_other_strategy(product, profile, used, trace, headed=False,
                                 on_event=None):
    for name in LADDER:
        if name == used:
            continue
        try:
            res, _ = _run_strategy(name, product, profile, trace, headed=headed,
                                   on_event=on_event)
            return res
        except ScrapeError:
            continue
    return None


def _record_failure(product, run, trace, duration_ms, tries, error, strategy,
                    outcome="failed", price=None):
    product.consecutive_failures += 1
    product.save(update_fields=["consecutive_failures"])
    attempt = ScrapeAttempt.objects.create(
        product=product, run=run, outcome=outcome, strategy=strategy,
        attempts=max(tries, 1), duration_ms=duration_ms,
        price_found=price, error=error or "all strategies failed", trace=trace,
    )
    _maybe_unhealthy_alert(product)
    return attempt


def _maybe_unhealthy_alert(product):
    if product.consecutive_failures in (3, 6, 12):
        Alert.objects.create(
            product=product, kind="scraper_unhealthy",
            message=(
                f"{product.name} has failed {product.consecutive_failures} scrapes in a "
                f"row. No price has been recorded for it since "
                f"{product.last_success_at or 'never'}."
            ),
        )


def _emit_change_alerts(product, previous_price, previous_stock, point):
    if previous_price is not None and previous_price > 0:
        delta = (point.price - previous_price) / previous_price
        if delta <= Decimal("-0.05"):
            Alert.objects.create(
                product=product, kind="price_drop",
                message=(
                    f"{product.name} dropped {abs(delta):.0%}, from "
                    f"{previous_price} to {point.price}."
                ),
            )
        elif delta >= Decimal("0.15"):
            Alert.objects.create(
                product=product, kind="price_rise",
                message=(
                    f"{product.name} rose {delta:.0%}, from {previous_price} "
                    f"to {point.price}."
                ),
            )

    if previous_stock is False and point.in_stock is True:
        Alert.objects.create(product=product, kind="back_in_stock",
                             message=f"{product.name} is back in stock.")
    elif previous_stock is True and point.in_stock is False:
        Alert.objects.create(product=product, kind="out_of_stock",
                             message=f"{product.name} has gone out of stock.")


# ---------------------------------------------------------------- whole run
def run_scheduled_scrape(trigger="cron", force=False, headed=False, product_ids=None,
                         on_event=None):
    run = ScrapeRun.objects.create(trigger=trigger)

    qs = Product.objects.filter(is_active=True)
    if product_ids:
        qs = qs.filter(id__in=product_ids)

    products = [p for p in qs if force or product_ids or p.is_due()]

    ok = failed = 0
    for product in products:
        if on_event:
            on_event(f"--- {product.name} ---")
        attempt = scrape_product(product, run=run, headed=headed, on_event=on_event)
        if attempt.outcome in ("success", "retried"):
            ok += 1
        else:
            failed += 1

    run.products_attempted = len(products)
    run.products_succeeded = ok
    run.products_failed = failed
    run.finished_at = timezone.now()
    run.save()
    return run


# ---------------------------------------------------------------- catalogue
def search_catalogue(query, limit=25):
    """
    Search the store. Lightweight path first; browser only if we have to.
    Returns a list of plain dicts for the frontend.
    """
    profile = get_profile()
    records, source = [], ""

    if profile.list_endpoint:
        try:
            records = http_strategy.list_products_via_json(profile)
            source = "cached-json"
        except ScrapeError as exc:
            log.warning("cached endpoint failed (%s), re-discovering", exc.message)
            records = []

    if not records:
        url, records = http_strategy.probe_candidate_endpoints()
        if records:
            source = "probed-json"
            profile.list_endpoint = url
            profile.save(update_fields=["list_endpoint", "updated_at"])

    if not records and settings.PLAYWRIGHT_ENABLED:
        records, endpoint = browser_strategy.discover_catalogue()
        source = "browser"
        if endpoint:
            profile.list_endpoint = endpoint
            profile.notes = "discovered during catalogue search"
            profile.save(update_fields=["list_endpoint", "notes", "updated_at"])

    if not records:
        raise ScrapeError("could not read the store catalogue", retryable=True)

    q = (query or "").strip().lower()
    out = []
    for rec in records:
        name = str(http_strategy._pick(rec, http_strategy._NAME_KEYS) or "").strip()
        if not name:
            continue
        if q and q not in name.lower():
            continue
        pid = http_strategy.record_identity(rec) or name
        raw_price = http_strategy._pick(rec, http_strategy._PRICE_KEYS)
        url = rec.get("url") or rec.get("link") or f"{config.STORE_BASE_URL}/product/{pid}"
        if isinstance(url, str) and url.startswith("/"):
            url = config.STORE_BASE_URL + url
        out.append(
            {
                "store_product_id": str(pid),
                "name": name,
                "price": str(raw_price) if raw_price is not None else "",
                "url": url,
                "image_url": str(http_strategy._pick(rec, http_strategy._IMG_KEYS) or ""),
                "category": str(http_strategy._pick(rec, http_strategy._CAT_KEYS) or ""),
                "source": source,
            }
        )
        if len(out) >= limit:
            break
    return out
