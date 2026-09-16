"""Бизнес-логика автопарка.

Слой между маршрутом и хранилищем: правила предметной области (уникальность
госномера, фильтрация, пагинация) живут здесь и ничего не знают ни об HTTP,
ни о том, как именно данные лежат в хранилище.
"""
from decimal import Decimal

from .errors import CarNotFound, PlateAlreadyUsed
from .repository import repository


def list_cars(*, page=1, limit=10, status=None, brand=None, max_price_per_minute=None):
    """Отфильтрованный и постранично ограниченный список машин."""
    cars = repository.list()

    if status:
        cars = [c for c in cars if c["status"] == status]
    if brand:
        needle = brand.strip().lower()
        cars = [c for c in cars if needle in c["brand"].lower()]
    if max_price_per_minute is not None:
        ceiling = Decimal(str(max_price_per_minute))
        cars = [c for c in cars if Decimal(str(c["price_per_minute"])) <= ceiling]

    cars.sort(key=lambda c: c["created_at"])

    total = len(cars)
    start = (page - 1) * limit
    return {
        "items": cars[start:start + limit],
        "page": page,
        "limit": limit,
        "total": total,
        "pages": (total + limit - 1) // limit if total else 0,
    }


def get_car(car_id):
    car = repository.get(car_id)
    if car is None:
        raise CarNotFound(details=[{"field": "id", "message": f"No car with id {car_id}"}])
    return car


def create_car(data):
    _ensure_plate_free(data["plate"])
    return repository.add(data)


def replace_car(car_id, data):
    car = get_car(car_id)
    _ensure_plate_free(data["plate"], exclude_id=car["id"])
    return repository.replace(car["id"], data)


def patch_car(car_id, changes):
    car = get_car(car_id)
    if "plate" in changes:
        _ensure_plate_free(changes["plate"], exclude_id=car["id"])
    return repository.update(car["id"], changes)


def delete_car(car_id):
    car = get_car(car_id)
    repository.delete(car["id"])


def _ensure_plate_free(plate, exclude_id=None):
    existing = repository.find_by_plate(plate)
    if existing and existing["id"] != exclude_id:
        raise PlateAlreadyUsed(details=[{"field": "plate", "message": f"Plate {plate} is already registered"}])
