from django.urls import path

from .views import CarCollectionView, CarItemView

urlpatterns = [
    path("cars", CarCollectionView.as_view(), name="car-collection"),
    path("cars/<str:car_id>", CarItemView.as_view(), name="car-item"),
]
