"""
Ground truth for the reveal-price interaction.

    python manage.py inspect_reveal --product 111
    python manage.py inspect_reveal --url https://demo.inelabteamdev.com/product/111 --headed

Navigates to one product, performs the exact hover-and-click the scraper
does, and saves what actually happened to backend/debug/reveal.*:

    reveal.before.html    the DOM right after the page loads
    reveal.after.html     the DOM ~1s after the reveal click
    reveal.network.json   every response the page made, with status + type
    reveal.events.txt     a log of what the reveal logic did, and the
                           price text it ended up finding (or the error)

Use this when scrape_headed still can't find a price: open reveal.after.html
and search for the number you'd expect to see, or diff it against
reveal.before.html to see exactly what the click changed.
"""

import os

from django.core.management.base import BaseCommand, CommandError

from tracker.models import Product
from tracker.scraper import browser_strategy, config


class Command(BaseCommand):
    help = "Dump the DOM before/after the reveal-price interaction for one product."

    def add_arguments(self, parser):
        parser.add_argument("--product", type=int, help="Tracked product id")
        parser.add_argument("--url", type=str, help="Product URL, if not tracked yet")
        parser.add_argument("--headed", action="store_true")

    def handle(self, *args, **opts):
        if opts.get("url"):
            url = opts["url"]
        elif opts.get("product"):
            try:
                url = Product.objects.get(pk=opts["product"]).url
            except Product.DoesNotExist as exc:
                raise CommandError(f"No product with id {opts['product']}") from exc
        else:
            raise CommandError("Pass --product <id> or --url <product url>")

        os.makedirs("debug", exist_ok=True)
        path = "debug/reveal"

        self.stdout.write(f"Inspecting: {url}")
        price_text, events, network = browser_strategy.dump_debug_reveal(
            path, url, headed=opts["headed"]
        )

        for line in events:
            self.stdout.write(line)

        self.stdout.write("")
        if price_text.startswith("<<not found"):
            self.stdout.write(self.style.ERROR(f"Result: {price_text}"))
        else:
            self.stdout.write(self.style.SUCCESS(f"Result: found {price_text!r}"))

        json_calls = [n for n in network if "json" in (n[2] or "").lower()]
        self.stdout.write(f"\nJSON responses seen ({len(json_calls)}):")
        for u, s, c in json_calls:
            self.stdout.write(f"  [{s}] {u}")

        self.stdout.write(self.style.SUCCESS(
            f"\nSaved {path}.before.html, {path}.after.html, "
            f"{path}.network.json, {path}.events.txt — open the .after.html "
            "one and search for the price you expect to see."
        ))