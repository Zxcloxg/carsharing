"""URL configuration for the booking-service.

Public REST API of the carsharing core domain under /api/v1/:
authentication and API users (accounts/urls.py), cars, customers,
bookings and payments (fleet/urls.py), background task status (jobs/urls.py).
"""
from django.contrib import admin
from django.urls import include, path

from health.views import health, live, metrics

urlpatterns = [
    path("health", health, name="health"),
    path("health/", health),
    path("health/live", live, name="health-live"),
    path("metrics", metrics, name="metrics"),
    path("api/v1/", include("accounts.urls")),
    path("api/v1/", include("fleet.urls")),
    path("api/v1/", include("jobs.urls")),
    path("admin/", admin.site.urls),
]
