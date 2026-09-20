from rest_framework import serializers

from .models import Alert, PricePoint, Product, ScrapeAttempt, ScrapeRun


class PricePointSerializer(serializers.ModelSerializer):
    class Meta:
        model = PricePoint
        fields = ["id", "price", "in_stock", "stock_text", "strategy", "scraped_at"]


class ScrapeAttemptSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScrapeAttempt
        fields = [
            "id", "outcome", "strategy", "attempts", "duration_ms",
            "price_found", "in_stock_found", "error", "trace", "created_at",
        ]


class ProductSerializer(serializers.ModelSerializer):
    points_recorded = serializers.SerializerMethodField()
    reliability = serializers.SerializerMethodField()

    class Meta:
        model = Product
        fields = [
            "id", "store_product_id", "name", "url", "image_url", "category",
            "currency", "preferred_strategy", "interval_minutes", "is_active",
            "last_price", "last_in_stock", "last_success_at",
            "consecutive_failures", "created_at", "points_recorded", "reliability",
        ]
        read_only_fields = [
            "last_price", "last_in_stock", "last_success_at",
            "consecutive_failures", "preferred_strategy",
        ]

    def get_points_recorded(self, obj):
        return obj.price_points.count()

    def get_reliability(self, obj):
        recent = list(obj.attempts.values_list("outcome", flat=True)[:25])
        if not recent:
            return None
        good = sum(1 for o in recent if o in ("success", "retried"))
        return round(100 * good / len(recent))


class AlertSerializer(serializers.ModelSerializer):
    product_name = serializers.CharField(source="product.name", default="", read_only=True)

    class Meta:
        model = Alert
        fields = ["id", "kind", "message", "created_at", "acknowledged",
                  "product", "product_name"]


class ScrapeRunSerializer(serializers.ModelSerializer):
    class Meta:
        model = ScrapeRun
        fields = ["id", "trigger", "started_at", "finished_at",
                  "products_attempted", "products_succeeded", "products_failed"]
