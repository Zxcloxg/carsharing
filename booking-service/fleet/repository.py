"""Слой хранения.

По условию ПР2 полноценная база данных появится в ПР3, поэтому здесь —
потокобезопасное хранилище в памяти процесса. Остальной код работает только
через этот интерфейс, так что замена на Django ORM в ПР3 не заденет ни слой
бизнес-логики, ни маршруты.
"""
import threading
import uuid
from datetime import datetime, timezone


def _now():
    return datetime.now(timezone.utc).isoformat()


class InMemoryCarRepository:
    def __init__(self):
        self._cars: dict[str, dict] = {}
        self._lock = threading.Lock()

    # --- чтение -----------------------------------------------------------
    def list(self):
        with self._lock:
            return [dict(car) for car in self._cars.values()]

    def get(self, car_id):
        with self._lock:
            car = self._cars.get(str(car_id))
            return dict(car) if car else None

    def find_by_plate(self, plate):
        with self._lock:
            for car in self._cars.values():
                if car["plate"] == plate:
                    return dict(car)
            return None

    # --- запись -----------------------------------------------------------
    def add(self, data):
        car = dict(data)
        car["id"] = str(uuid.uuid4())
        car["created_at"] = car["updated_at"] = _now()
        with self._lock:
            self._cars[car["id"]] = car
        return dict(car)

    def replace(self, car_id, data):
        with self._lock:
            stored = self._cars[str(car_id)]
            car = dict(data)
            car["id"] = stored["id"]
            car["created_at"] = stored["created_at"]
            car["updated_at"] = _now()
            self._cars[car["id"]] = car
            return dict(car)

    def update(self, car_id, changes):
        with self._lock:
            car = self._cars[str(car_id)]
            car.update(changes)
            car["updated_at"] = _now()
            return dict(car)

    def delete(self, car_id):
        with self._lock:
            self._cars.pop(str(car_id), None)

    def clear(self):
        with self._lock:
            self._cars.clear()


repository = InMemoryCarRepository()


def seed_demo_cars():
    """Несколько машин для ручной проверки API сразу после запуска."""
    if repository.list():
        return
    demo = [
        {"plate": "123ABC02", "brand": "Kia", "model": "Rio", "year": 2022,
         "color": "white", "price_per_minute": 65, "fuel_level": 0.8, "status": "free"},
        {"plate": "456DEF02", "brand": "Hyundai", "model": "Creta", "year": 2023,
         "color": "grey", "price_per_minute": 95, "fuel_level": 0.55, "status": "free"},
        {"plate": "789GHI02", "brand": "Kia", "model": "Seltos", "year": 2021,
         "color": "blue", "price_per_minute": 90, "fuel_level": 0.3, "status": "service"},
    ]
    for car in demo:
        repository.add(car)
