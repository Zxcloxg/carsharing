"""URL configuration for the booking-service.

Public REST API of the carsharing core domain under /api/v1/:
authentication and API users (accounts/urls.py), cars, customers and
bookings (fleet/urls.py).
"""
from django.contrib import admin
from django.urls import include, path
from health.views import health

urlpatterns = [
    path("health", health, name="health"),
    path("health/", health),
    path("api/v1/", include("accounts.urls")),
    path("api/v1/", include("fleet.urls")),
    path("admin/", admin.site.urls),
]
