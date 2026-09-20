"""
Reconnaissance. Run this once, before anything else.

    python manage.py probe_store

It opens the store in a browser, waits for it to finish loading, then reports:
  * every JSON response the page fetched for itself (candidate API endpoints)
  * which of the scraper's candidate selectors actually match
  * a saved copy of the rendered HTML at debug/rendered.html

Use the output to confirm the scraper is looking in the right places. If a
selector group shows MISSING, add the real one to tracker/scraper/config.py.
"""

import json
import os

from bs4 import BeautifulSoup
from django.core.management.base import BaseCommand

from tracker.scraper import browser_strategy, config, http_strategy, parsing
from tracker.scraper.engine import get_profile


class Command(BaseCommand):
    help = "Inspect the mock store and report what the scraper can see."

    def add_arguments(self, parser):
        parser.add_argument("--headed", action="store_true")

    def handle(self, *args, **opts):
        os.makedirs("debug", exist_ok=True)
        path = "debug/rendered.html"

        self.stdout.write(f"Store: {config.STORE_BASE_URL}")
        self.stdout.write("Step 1 — blind-probing common API paths…")
        url, records = http_strategy.probe_candidate_endpoints()
        if url:
            self.stdout.write(self.style.SUCCESS(
                f"  found {len(records)} records at {url}"))
        else:
            self.stdout.write("  nothing at the usual paths (expected for an SPA)")

        self.stdout.write("Step 2 — rendering the page in Chromium…")
        html, network = browser_strategy.dump_debug_html(path, headed=opts["headed"])
        self.stdout.write(f"  saved rendered HTML to {path} ({len(html)} bytes)")

        self.stdout.write("Step 3 — JSON responses the page fetched itself:")
        json_calls = [n for n in network if "json" in (n[2] or "").lower()]
        if not json_calls:
            self.stdout.write("  none — the data is probably embedded in the bundle")
        for u, s, c in json_calls[:25]:
            self.stdout.write(f"  [{s}] {u}")

        self.stdout.write("Step 4 — selector check against the rendered DOM:")
        soup = BeautifulSoup(html, "lxml")
        for label, sels in (("price", config.PRICE_SELECTORS),
                            ("stock", config.STOCK_SELECTORS),
                            ("name", config.NAME_SELECTORS),
                            ("card", config.CARD_SELECTORS)):
            hits = [(s, len(soup.select(s))) for s in sels if soup.select(s)]
            if hits:
                self.stdout.write(self.style.SUCCESS(
                    f"  {label}: " + ", ".join(f"{s} ({n})" for s, n in hits[:4])))
            else:
                self.stdout.write(self.style.ERROR(f"  {label}: MISSING"))

        text, sel = parsing.first_text(soup, config.PRICE_SELECTORS)
        if text:
            self.stdout.write(f"  first price text: {text!r} via {sel} "
                              f"-> parsed {parsing.parse_price(text)}")

        if url or json_calls:
            profile = get_profile()
            profile.list_endpoint = url or json_calls[0][0]
            profile.notes = "set by probe_store"
            profile.save()
            self.stdout.write(self.style.SUCCESS(
                f"Saved catalogue endpoint to SiteProfile: {profile.list_endpoint}"))

        with open("debug/summary.json", "w") as fh:
            json.dump({"network": [{"url": u, "status": s, "type": c}
                                   for u, s, c in network]}, fh, indent=2)
        self.stdout.write(self.style.SUCCESS("Done. See debug/ for the artefacts."))
