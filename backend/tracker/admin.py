from django.contrib import admin

from .models import Alert, PricePoint, Product, ScrapeAttempt, ScrapeRun, SiteProfile


@admin.register(Product)
class ProductAdmin(admin.ModelAdmin):
    list_display = ("name", "last_price", "last_in_stock", "preferred_strategy",
                    "consecutive_failures", "last_success_at", "is_active")
    search_fields = ("name", "store_product_id")


@admin.register(PricePoint)
class PricePointAdmin(admin.ModelAdmin):
    list_display = ("product", "price", "in_stock", "strategy", "scraped_at")
    list_filter = ("strategy", "in_stock")


@admin.register(ScrapeAttempt)
class ScrapeAttemptAdmin(admin.ModelAdmin):
    list_display = ("product", "outcome", "strategy", "attempts",
                    "duration_ms", "created_at")
    list_filter = ("outcome", "strategy")


admin.site.register([ScrapeRun, Alert, SiteProfile])
