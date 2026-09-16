"""Маршруты REST API автопарка.

Слой тонкий: разобрать запрос -> провалидировать -> вызвать сервис -> отдать
ответ с нужным кодом состояния. Никакой бизнес-логики здесь нет.
"""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import services
from .serializers import CarListQuerySerializer, CarPatchSerializer, CarSerializer


class CarCollectionView(APIView):
    """/api/v1/cars — список и создание."""

    def get(self, request):
        query = CarListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response(services.list_cars(**query.validated_data), status=status.HTTP_200_OK)

    def post(self, request):
        body = CarSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        car = services.create_car(body.validated_data)
        return Response(car, status=status.HTTP_201_CREATED, headers={"Location": f"/api/v1/cars/{car['id']}"})


class CarItemView(APIView):
    """/api/v1/cars/{id} — чтение, замена, частичное обновление, удаление."""

    def get(self, request, car_id):
        return Response(services.get_car(car_id), status=status.HTTP_200_OK)

    def put(self, request, car_id):
        body = CarSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return Response(services.replace_car(car_id, body.validated_data), status=status.HTTP_200_OK)

    def patch(self, request, car_id):
        body = CarPatchSerializer(data=request.data, partial=True)
        body.is_valid(raise_exception=True)
        return Response(services.patch_car(car_id, body.validated_data), status=status.HTTP_200_OK)

    def delete(self, request, car_id):
        services.delete_car(car_id)
        return Response(status=status.HTTP_204_NO_CONTENT)
