"""
Deliberate fault injection, used only for the observable run.

The mock store is slow and flaky on its own schedule, which is no good for a
2-4 minute recording. This module makes the failure modes reproducible: it
wraps the HTTP fetcher and installs a Playwright route handler that randomly
stalls requests or returns 503, so retry and recovery can be demonstrated on
camera rather than waited for.

It is off unless `enable()` is called, and nothing in the scheduled path
touches it.
"""

import random
import time

_enabled = False
_rate = 0.4
_original_fetch = None


def is_enabled():
    return _enabled


def enable(rate=0.4):
    global _enabled, _rate, _original_fetch
    from . import http_strategy
    from .result import ScrapeError

    _enabled, _rate = True, rate

    if _original_fetch is None:
        _original_fetch = http_strategy.fetch

        def flaky_fetch(url, expect_json=False, timeout=None):
            roll = random.random()
            if roll < _rate * 0.5:
                time.sleep(random.uniform(3.0, 6.0))
                raise ScrapeError("injected fault: upstream stalled", retryable=True)
            if roll < _rate:
                raise ScrapeError("injected fault: HTTP 503", retryable=True, status=503)
            return _original_fetch(url, expect_json=expect_json, timeout=timeout)

        http_strategy.fetch = flaky_fetch


def disable():
    global _enabled, _original_fetch
    from . import http_strategy

    _enabled = False
    if _original_fetch is not None:
        http_strategy.fetch = _original_fetch
        _original_fetch = None


def install_route(page):
    """Randomly delay or fail sub-resource requests inside the browser."""
    if not _enabled:
        return

    def handler(route, request):
        roll = random.random()
        if request.resource_type in ("document",):
            return route.continue_()
        if roll < _rate * 0.45:
            time.sleep(random.uniform(1.2, 3.0))
            return route.continue_()
        if roll < _rate * 0.65:
            return route.fulfill(status=503, body="injected fault")
        return route.continue_()

    try:
        page.route("**/*", handler)
    except Exception:
        pass
