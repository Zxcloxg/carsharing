"""URL configuration for the booking-service.

Public REST API of the carsharing core domain (users, cars, bookings).
Only /health is wired in the skeleton; domain endpoints are added in later works.
"""
from django.contrib import admin
from django.urls import path
from health.views import health

urlpatterns = [
    path("health", health, name="health"),
    path("health/", health),
    path("admin/", admin.site.urls),
]
