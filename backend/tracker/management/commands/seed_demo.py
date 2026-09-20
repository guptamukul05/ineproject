"""Track the first few products from the store, so there is data to look at."""

from django.conf import settings
from django.core.management.base import BaseCommand

from tracker.models import Product
from tracker.scraper import engine


class Command(BaseCommand):
    help = "Track the first N products found in the store catalogue."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=4)

    def handle(self, *args, **opts):
        results = engine.search_catalogue("", limit=opts["count"])
        if not results:
            self.stdout.write(self.style.ERROR("Catalogue came back empty."))
            return
        for r in results:
            product, created = Product.objects.get_or_create(
                store_product_id=r["store_product_id"],
                defaults={
                    "name": r["name"],
                    "url": r["url"],
                    "image_url": r["image_url"],
                    "category": r["category"],
                    "interval_minutes": settings.SCRAPE_INTERVAL_MINUTES,
                },
            )
            self.stdout.write(("tracked " if created else "already tracking ") + product.name)
        run = engine.run_scheduled_scrape(trigger="manual", force=True)
        self.stdout.write(self.style.SUCCESS(
            f"Seed run: {run.products_succeeded} ok, {run.products_failed} failed"))
