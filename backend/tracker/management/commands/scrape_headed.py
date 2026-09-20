"""
The observable run.

    python manage.py scrape_headed
    python manage.py scrape_headed --product 3
    python manage.py scrape_headed --chaos

A real Chromium window opens, slowed down so a human can follow it, with a HUD
drawn on the page showing each decision the scraper makes. `--chaos` routes the
store through a fault injector that randomly stalls and fails requests, so the
recording can show retry and recovery behaviour on demand rather than waiting
for the store to misbehave on its own.
"""

from django.core.management.base import BaseCommand

from tracker.models import Product
from tracker.scraper import engine


class Command(BaseCommand):
    help = "Run the scraper in a visible browser window."

    def add_arguments(self, parser):
        parser.add_argument("--product", type=int, help="Scrape one product by id")
        parser.add_argument("--chaos", action="store_true",
                            help="Inject slow and failing responses")
        parser.add_argument("--chaos-rate", type=float, default=0.45)

    def handle(self, *args, **opts):
        if opts["chaos"]:
            from tracker.scraper import chaos

            chaos.enable(rate=opts["chaos_rate"])
            self.stdout.write(self.style.WARNING(
                f"Fault injection ON (rate={opts['chaos_rate']}). "
                "Requests will be randomly delayed or failed."
            ))

        ids = [opts["product"]] if opts.get("product") else None
        if not Product.objects.filter(is_active=True).exists():
            self.stdout.write(self.style.ERROR(
                "No tracked products yet. Track one from the UI first, or run "
                "`python manage.py seed_demo`."
            ))
            return

        def on_event(msg, tone="info"):
            style = {
                "bad": self.style.ERROR,
                "warn": self.style.WARNING,
                "good": self.style.SUCCESS,
            }.get(tone, lambda s: s)
            self.stdout.write(style(msg))

        run = engine.run_scheduled_scrape(
            trigger="headed", force=True, headed=True,
            product_ids=ids, on_event=on_event,
        )
        self.stdout.write(self.style.SUCCESS(
            f"\nRun #{run.id} finished — {run.products_succeeded} ok, "
            f"{run.products_failed} failed, out of {run.products_attempted}."
        ))
