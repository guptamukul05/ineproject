"""Headless scrape from the command line. Same code path as the cron endpoint."""

from django.core.management.base import BaseCommand

from tracker.scraper import engine


class Command(BaseCommand):
    help = "Run a scheduled scrape headlessly."

    def add_arguments(self, parser):
        parser.add_argument("--force", action="store_true",
                            help="Scrape every product, ignoring the interval")
        parser.add_argument("--product", type=int)

    def handle(self, *args, **opts):
        ids = [opts["product"]] if opts.get("product") else None
        run = engine.run_scheduled_scrape(
            trigger="manual", force=opts["force"], product_ids=ids,
            on_event=lambda m, t="info": self.stdout.write(m),
        )
        self.stdout.write(self.style.SUCCESS(
            f"Run #{run.id}: {run.products_succeeded} ok / "
            f"{run.products_failed} failed / {run.products_attempted} attempted"
        ))
