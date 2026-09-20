from django.db import models
from django.utils import timezone


class SiteProfile(models.Model):
    """
    What the scraper has *learned* about the store, cached between runs.

    The store is JavaScript-rendered, so on the first browser run we watch the
    network traffic and remember any JSON endpoint the page itself calls. Later
    runs can then hit that endpoint over plain HTTP and skip the browser
    entirely. This is what lets the app satisfy "prefer lightweight fetching"
    against a site that, on a cold read, looks like it needs a browser.
    """

    key = models.CharField(max_length=64, unique=True, default="default")
    list_endpoint = models.CharField(max_length=500, blank=True, default="")
    detail_endpoint_template = models.CharField(max_length=500, blank=True, default="")
    json_shape = models.JSONField(default=dict, blank=True)
    dom_fingerprint = models.CharField(max_length=64, blank=True, default="")
    notes = models.TextField(blank=True, default="")
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"SiteProfile({self.key})"


class Product(models.Model):
    STRATEGIES = [
        ("json_api", "Cached JSON endpoint"),
        ("http_html", "HTTP + HTML parse"),
        ("browser", "Headless browser"),
    ]

    store_product_id = models.CharField(max_length=120, unique=True)
    name = models.CharField(max_length=300)
    url = models.URLField(max_length=600)
    image_url = models.URLField(max_length=600, blank=True, default="")
    category = models.CharField(max_length=120, blank=True, default="")
    currency = models.CharField(max_length=8, blank=True, default="")

    # Adaptive routing: the strategy that worked last time is tried first.
    preferred_strategy = models.CharField(
        max_length=20, choices=STRATEGIES, default="json_api"
    )
    dom_fingerprint = models.CharField(max_length=64, blank=True, default="")

    interval_minutes = models.PositiveIntegerField(default=120)
    is_active = models.BooleanField(default=True)

    last_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    last_in_stock = models.BooleanField(null=True, blank=True)
    last_success_at = models.DateTimeField(null=True, blank=True)
    consecutive_failures = models.PositiveIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name

    def is_due(self, now=None):
        now = now or timezone.now()
        if not self.is_active:
            return False
        if self.last_success_at is None:
            return True
        age = (now - self.last_success_at).total_seconds() / 60.0
        return age >= self.interval_minutes


class PricePoint(models.Model):
    """One *verified* observation. Failures never land here."""

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="price_points"
    )
    price = models.DecimalField(max_digits=12, decimal_places=2)
    in_stock = models.BooleanField(null=True, blank=True)
    stock_text = models.CharField(max_length=120, blank=True, default="")
    strategy = models.CharField(max_length=20, default="")
    scraped_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["scraped_at"]

    def __str__(self):
        return f"{self.product.name} @ {self.price} ({self.scraped_at:%Y-%m-%d %H:%M})"


class ScrapeRun(models.Model):
    TRIGGERS = [("cron", "Cron"), ("manual", "Manual"), ("headed", "Headed")]

    trigger = models.CharField(max_length=20, choices=TRIGGERS, default="cron")
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    products_attempted = models.PositiveIntegerField(default=0)
    products_succeeded = models.PositiveIntegerField(default=0)
    products_failed = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-started_at"]


class ScrapeAttempt(models.Model):
    """
    One row per *scrape of one product* (not per retry). `attempts` says how
    many HTTP/browser tries it took, and `trace` holds the per-try detail so
    the log can be honest about what actually happened.
    """

    OUTCOMES = [
        ("success", "Success"),
        ("retried", "Succeeded after retry"),
        ("failed", "Failed"),
        ("rejected", "Rejected by validation"),
    ]

    product = models.ForeignKey(
        Product, on_delete=models.CASCADE, related_name="attempts"
    )
    run = models.ForeignKey(
        ScrapeRun,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="attempts",
    )
    outcome = models.CharField(max_length=20, choices=OUTCOMES, db_index=True)
    strategy = models.CharField(max_length=20, blank=True, default="")
    attempts = models.PositiveIntegerField(default=1)
    duration_ms = models.PositiveIntegerField(default=0)
    price_found = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True
    )
    in_stock_found = models.BooleanField(null=True, blank=True)
    error = models.TextField(blank=True, default="")
    trace = models.JSONField(default=list, blank=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        ordering = ["-created_at"]


class Alert(models.Model):
    KINDS = [
        ("price_drop", "Price drop"),
        ("price_rise", "Price rise"),
        ("back_in_stock", "Back in stock"),
        ("out_of_stock", "Out of stock"),
        ("structure_change", "Page structure changed"),
        ("scraper_unhealthy", "Scraper unhealthy"),
    ]

    product = models.ForeignKey(
        Product,
        on_delete=models.CASCADE,
        related_name="alerts",
        null=True,
        blank=True,
    )
    kind = models.CharField(max_length=30, choices=KINDS)
    message = models.TextField()
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    acknowledged = models.BooleanField(default=False)

    class Meta:
        ordering = ["-created_at"]
