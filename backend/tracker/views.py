import logging
import threading

from django.conf import settings
from django.db.models import Count, Q
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view
from rest_framework.response import Response

from .models import Alert, Product, ScrapeAttempt, ScrapeRun
from .scraper import engine
from .scraper.result import ScrapeError
from .serializers import (
    AlertSerializer,
    PricePointSerializer,
    ProductSerializer,
    ScrapeAttemptSerializer,
    ScrapeRunSerializer,
)

log = logging.getLogger("tracker.views")


@api_view(["GET"])
def health(request):
    last_run = ScrapeRun.objects.first()
    return Response(
        {
            "status": "ok",
            "time": timezone.now(),
            "store": settings.STORE_BASE_URL,
            "interval_minutes": settings.SCRAPE_INTERVAL_MINUTES,
            "playwright_enabled": settings.PLAYWRIGHT_ENABLED,
            "tracked_products": Product.objects.filter(is_active=True).count(),
            "last_run": ScrapeRunSerializer(last_run).data if last_run else None,
        }
    )


@api_view(["GET"])
def store_search(request):
    query = request.query_params.get("q", "").strip()
    try:
        results = engine.search_catalogue(query)
    except ScrapeError as exc:
        return Response(
            {"detail": f"Could not read the store catalogue: {exc.message}"},
            status=status.HTTP_502_BAD_GATEWAY,
        )
    tracked = set(Product.objects.values_list("store_product_id", flat=True))
    for r in results:
        r["already_tracked"] = r["store_product_id"] in tracked
    return Response({"query": query, "count": len(results), "results": results})


@api_view(["GET", "POST"])
def products(request):
    if request.method == "GET":
        qs = Product.objects.all()
        return Response(ProductSerializer(qs, many=True).data)

    data = request.data
    pid = str(data.get("store_product_id") or "").strip()
    name = str(data.get("name") or "").strip()
    if not pid or not name:
        return Response({"detail": "store_product_id and name are required."},
                        status=status.HTTP_400_BAD_REQUEST)

    product, created = Product.objects.get_or_create(
        store_product_id=pid,
        defaults={
            "name": name,
            "url": data.get("url") or f"{settings.STORE_BASE_URL}/product/{pid}",
            "image_url": data.get("image_url") or "",
            "category": data.get("category") or "",
            "interval_minutes": int(data.get("interval_minutes")
                                    or settings.SCRAPE_INTERVAL_MINUTES),
        },
    )
    if not created:
        return Response(
            {"detail": "Already tracking this product.",
             "product": ProductSerializer(product).data},
            status=status.HTTP_200_OK,
        )

    # first reading immediately, so the dashboard is not empty
    threading.Thread(
        target=_safe_scrape, args=(product.id,), daemon=True
    ).start()

    return Response(ProductSerializer(product).data, status=status.HTTP_201_CREATED)


def _safe_scrape(product_id):
    from django.db import connection

    try:
        product = Product.objects.get(id=product_id)
        engine.scrape_product(product, run=None)
    except Exception:
        log.exception("initial scrape failed for product %s", product_id)
    finally:
        connection.close()


@api_view(["GET", "PATCH", "DELETE"])
def product_detail(request, pk):
    try:
        product = Product.objects.get(pk=pk)
    except Product.DoesNotExist:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)

    if request.method == "DELETE":
        product.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

    if request.method == "PATCH":
        if "interval_minutes" in request.data:
            product.interval_minutes = max(15, int(request.data["interval_minutes"]))
        if "is_active" in request.data:
            product.is_active = bool(request.data["is_active"])
        product.save()

    return Response(ProductSerializer(product).data)


@api_view(["GET"])
def product_history(request, pk):
    try:
        product = Product.objects.get(pk=pk)
    except Product.DoesNotExist:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
    points = product.price_points.order_by("scraped_at")
    return Response(
        {"product": ProductSerializer(product).data,
         "points": PricePointSerializer(points, many=True).data}
    )


@api_view(["GET"])
def product_logs(request, pk):
    try:
        product = Product.objects.get(pk=pk)
    except Product.DoesNotExist:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
    limit = min(int(request.query_params.get("limit", 60)), 300)
    attempts = product.attempts.all()[:limit]
    summary = product.attempts.aggregate(
        total=Count("id"),
        success=Count("id", filter=Q(outcome="success")),
        retried=Count("id", filter=Q(outcome="retried")),
        failed=Count("id", filter=Q(outcome="failed")),
        rejected=Count("id", filter=Q(outcome="rejected")),
    )
    return Response({"summary": summary,
                     "attempts": ScrapeAttemptSerializer(attempts, many=True).data})


@api_view(["POST"])
def scrape_now(request, pk):
    try:
        product = Product.objects.get(pk=pk)
    except Product.DoesNotExist:
        return Response({"detail": "Not found."}, status=status.HTTP_404_NOT_FOUND)
    attempt = engine.scrape_product(product, run=None)
    return Response(ScrapeAttemptSerializer(attempt).data)


@api_view(["GET"])
def alerts(request):
    qs = Alert.objects.all()[:100]
    return Response(AlertSerializer(qs, many=True).data)


@api_view(["POST"])
def acknowledge_alert(request, pk):
    Alert.objects.filter(pk=pk).update(acknowledged=True)
    return Response({"ok": True})


@api_view(["GET"])
def runs(request):
    return Response(ScrapeRunSerializer(ScrapeRun.objects.all()[:40], many=True).data)


@api_view(["GET", "POST"])
def cron_scrape(request):
    """
    Called by cron-job.org every 2 hours.

    Auth is a shared secret in a header or query string. GET is allowed so the
    endpoint works with cron services that only issue GET requests.
    """
    body_key = ""
    if request.method == "POST" and isinstance(request.data, dict):
        body_key = request.data.get("key", "")
    supplied = (
        request.headers.get("X-Cron-Key")
        or request.query_params.get("key")
        or body_key
    )
    if supplied != settings.CRON_SECRET:
        return Response({"detail": "Invalid cron key."},
                        status=status.HTTP_401_UNAUTHORIZED)

    force = request.query_params.get("force") == "1"
    run = engine.run_scheduled_scrape(trigger="cron", force=force)
    return Response(ScrapeRunSerializer(run).data)


@api_view(["GET"])
def dashboard(request):
    """One call that fills the whole front page."""
    products_qs = Product.objects.all()
    recent = ScrapeAttempt.objects.all()[:30]
    totals = ScrapeAttempt.objects.aggregate(
        total=Count("id"),
        success=Count("id", filter=Q(outcome="success")),
        retried=Count("id", filter=Q(outcome="retried")),
        failed=Count("id", filter=Q(outcome="failed")),
        rejected=Count("id", filter=Q(outcome="rejected")),
    )
    return Response(
        {
            "products": ProductSerializer(products_qs, many=True).data,
            "recent_attempts": ScrapeAttemptSerializer(recent, many=True).data,
            "totals": totals,
            "alerts": AlertSerializer(Alert.objects.filter(acknowledged=False)[:20],
                                      many=True).data,
            "last_run": ScrapeRunSerializer(ScrapeRun.objects.first()).data
            if ScrapeRun.objects.exists() else None,
        }
    )
