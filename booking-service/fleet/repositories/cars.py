"""Доступ к таблице cars: создание, чтение, обновление, удаление, поиск по фильтру.

Марка и модель хранятся в справочнике car_models; наружу машина отдаётся
плоским словарём с полями brand и model, как в контракте API.
"""
from django.db import transaction
from django.db.models import ProtectedError

from ..errors import CarHasBookings, PlateAlreadyUsed
from ..models import Car
from .common import iso, parse_id, save_in_savepoint

FIELDS = ("plate", "year", "color", "price_per_minute", "fuel_level", "status")


def to_dict(car):
    return {
        "plate": car.plate,
        "brand": car.car_model.brand,
        "model": car.car_model.model,
        **{field: getattr(car, field) for field in FIELDS[1:]},
        "id": str(car.id),
        "created_at": iso(car.created_at),
        "updated_at": iso(car.updated_at),
    }


class CarRepository:
    # --- чтение -----------------------------------------------------------
    def search(self, *, status=None, brand=None, max_price_per_minute=None, offset=0, limit=10):
        """Страница машин по фильтру и общее число подходящих записей."""
        query = Car.objects.select_related("car_model")
        if status:
            query = query.filter(status=status)
        if brand:
            query = query.filter(car_model__brand__icontains=brand.strip())
        if max_price_per_minute is not None:
            query = query.filter(price_per_minute__lte=max_price_per_minute)
        total = query.count()
        return [to_dict(car) for car in query.order_by("created_at")[offset:offset + limit]], total

    def get(self, car_id):
        car = self._find(car_id)
        return to_dict(car) if car else None

    def get_for_update(self, car_id):
        """Читает машину с блокировкой строки до конца текущей транзакции (SELECT ... FOR UPDATE)."""
        # of=("self",): блокируется только строка машины, а не общая строка справочника моделей.
        pk = parse_id(car_id)
        query = Car.objects.select_for_update(of=("self",)).select_related("car_model")
        car = query.filter(pk=pk).first() if pk else None
        return to_dict(car) if car else None

    def find_by_plate(self, plate):
        car = Car.objects.select_related("car_model").filter(plate=plate).first()
        return to_dict(car) if car else None

    # --- запись -----------------------------------------------------------
    def add(self, data, car_model_id):
        car = Car(car_model_id=car_model_id, **{field: data[field] for field in FIELDS if field in data})
        return to_dict(self._save(car))

    def replace(self, car_id, data, car_model_id):
        car = self._find(car_id)
        car.car_model_id = car_model_id
        for field in FIELDS:
            setattr(car, field, data.get(field, Car._meta.get_field(field).get_default()))
        return to_dict(self._save(car))

    def update(self, car_id, changes, car_model_id=None):
        car = self._find(car_id)
        if car_model_id is not None:
            car.car_model_id = car_model_id
        for field, value in changes.items():
            setattr(car, field, value)
        return to_dict(self._save(car))

    def delete(self, car_id):
        try:
            with transaction.atomic():
                Car.objects.filter(pk=parse_id(car_id)).delete()
        except ProtectedError:
            raise CarHasBookings(details=[{"field": "id", "message": f"Car {car_id} has bookings"}])

    # --- внутреннее -------------------------------------------------------
    def _find(self, car_id):
        pk = parse_id(car_id)
        return Car.objects.select_related("car_model").filter(pk=pk).first() if pk else None

    def _save(self, car):
        return save_in_savepoint(car, {
            "cars_plate_key": lambda: PlateAlreadyUsed(
                details=[{"field": "plate", "message": f"Plate {car.plate} is already registered"}]
            ),
        })
