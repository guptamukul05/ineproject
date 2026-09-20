from django.urls import path

from . import views

urlpatterns = [
    path("health/", views.health, name="health"),
    path("dashboard/", views.dashboard, name="dashboard"),
    path("store/search/", views.store_search, name="store-search"),
    path("products/", views.products, name="products"),
    path("products/<int:pk>/", views.product_detail, name="product-detail"),
    path("products/<int:pk>/history/", views.product_history, name="product-history"),
    path("products/<int:pk>/logs/", views.product_logs, name="product-logs"),
    path("products/<int:pk>/scrape/", views.scrape_now, name="product-scrape"),
    path("alerts/", views.alerts, name="alerts"),
    path("alerts/<int:pk>/ack/", views.acknowledge_alert, name="alert-ack"),
    path("runs/", views.runs, name="runs"),
    path("cron/scrape/", views.cron_scrape, name="cron-scrape"),
]
