from django.urls import path

from . import views

urlpatterns = [
    path("cars", views.CarCollectionView.as_view(), name="car-collection"),
    path("cars/<str:car_id>", views.CarItemView.as_view(), name="car-item"),
    path("cars/<str:car_id>/status-history", views.CarStatusHistoryView.as_view(), name="car-status-history"),
    path("customers", views.CustomerCollectionView.as_view(), name="customer-collection"),
    path("customers/<str:customer_id>", views.CustomerItemView.as_view(), name="customer-item"),
    path(
        "customers/<str:customer_id>/driver-licenses",
        views.CustomerLicenseCollectionView.as_view(),
        name="customer-licenses",
    ),
    path("driver-licenses/<str:license_id>", views.DriverLicenseItemView.as_view(), name="driver-license-item"),
    path("bookings", views.BookingCollectionView.as_view(), name="booking-collection"),
    path("bookings/<str:booking_id>", views.BookingItemView.as_view(), name="booking-item"),
    path("bookings/<str:booking_id>/payments", views.BookingPaymentsView.as_view(), name="booking-payments"),
]
