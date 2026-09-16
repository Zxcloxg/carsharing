"""URL configuration for the booking-service.

Public REST API of the carsharing core domain (users, cars, bookings).
Public REST of the fleet resource lives under /api/v1/ (see fleet/urls.py).
"""
from django.contrib import admin
from django.urls import include, path
from health.views import health

urlpatterns = [
    path("health", health, name="health"),
    path("health/", health),
    path("api/v1/", include("fleet.urls")),
    path("admin/", admin.site.urls),
]
