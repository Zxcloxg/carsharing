"""Бизнес-логика каршеринга.

Слой между маршрутом и хранилищем: правила предметной области (уникальность
госномера, допуск клиента к аренде, доступность машины, расчёт стоимости,
пагинация) и границы транзакций живут здесь и ничего не знают ни об HTTP,
ни о том, как именно данные лежат в базе.
"""
import math
import uuid
from datetime import datetime, timezone

from django.db import transaction

from .errors import (
    BookingAlreadyClosed,
    BookingNotFound,
    CarNotAvailable,
    CarNotFound,
    CustomerCannotRent,
    CustomerHasActiveBooking,
    CustomerNotFound,
    LicenseAlreadyReviewed,
    LicenseNotFound,
    PlateAlreadyUsed,
)
from .models import BookingStatus, CarStatus, CustomerStatus, LicenseStatus, PaymentStatus
from .repositories import (
    booking_repository,
    car_model_repository,
    car_repository,
    car_status_history_repository,
    customer_repository,
    driver_license_repository,
    payment_repository,
)


def _page(items, total, page, limit):
    return {
        "items": items,
        "page": page,
        "limit": limit,
        "total": total,
        "pages": (total + limit - 1) // limit if total else 0,
    }


def _now():
    return datetime.now(timezone.utc)


# --- машины ---------------------------------------------------------------
def list_cars(*, page=1, limit=10, status=None, brand=None, max_price_per_minute=None):
    """Отфильтрованный и постранично ограниченный список машин."""
    items, total = car_repository.search(
        status=status, brand=brand, max_price_per_minute=max_price_per_minute,
        offset=(page - 1) * limit, limit=limit,
    )
    return _page(items, total, page, limit)


def get_car(car_id):
    car = car_repository.get(car_id)
    if car is None:
        raise CarNotFound(details=[{"field": "id", "message": f"No car with id {car_id}"}])
    return car


@transaction.atomic
def create_car(data):
    _ensure_plate_free(data["plate"])
    car_model = car_model_repository.get_or_add(data["brand"], data["model"])
    car = car_repository.add(data, car_model_id=car_model["id"])
    _log_status(car["id"], None, car["status"])
    return car


@transaction.atomic
def replace_car(car_id, data):
    car = get_car(car_id)
    _ensure_plate_free(data["plate"], exclude_id=car["id"])
    car_model = car_model_repository.get_or_add(data["brand"], data["model"])
    updated = car_repository.replace(car["id"], data, car_model_id=car_model["id"])
    _log_status(car["id"], car["status"], updated["status"])
    return updated


@transaction.atomic
def patch_car(car_id, changes):
    car = get_car(car_id)
    changes = dict(changes)
    if "plate" in changes:
        _ensure_plate_free(changes["plate"], exclude_id=car["id"])
    car_model_id = None
    if "brand" in changes or "model" in changes:
        brand, model = changes.pop("brand", car["brand"]), changes.pop("model", car["model"])
        car_model_id = car_model_repository.get_or_add(brand, model)["id"]
    updated = car_repository.update(car["id"], changes, car_model_id=car_model_id)
    _log_status(car["id"], car["status"], updated["status"])
    return updated


def delete_car(car_id):
    car = get_car(car_id)
    car_repository.delete(car["id"])


def car_status_history(car_id):
    car = get_car(car_id)
    return {"items": car_status_history_repository.search(car_id=car["id"])}


def _ensure_plate_free(plate, exclude_id=None):
    existing = car_repository.find_by_plate(plate)
    if existing and existing["id"] != exclude_id:
        raise PlateAlreadyUsed(details=[{"field": "plate", "message": f"Plate {plate} is already registered"}])


def _log_status(car_id, old_status, new_status, booking_id=None):
    if old_status != new_status:
        car_status_history_repository.add(
            car_id=car_id, old_status=old_status, new_status=new_status, booking_id=booking_id
        )


# --- клиенты и водительские удостоверения -----------------------------------
def list_customers(*, page=1, limit=10, status=None):
    items, total = customer_repository.search(status=status, offset=(page - 1) * limit, limit=limit)
    return _page(items, total, page, limit)


def get_customer(customer_id):
    customer = customer_repository.get(customer_id)
    if customer is None:
        raise CustomerNotFound(details=[{"field": "id", "message": f"No customer with id {customer_id}"}])
    return customer


def create_customer(data):
    return customer_repository.add(data)


def patch_customer(customer_id, changes):
    customer = get_customer(customer_id)
    return customer_repository.update(customer["id"], changes)


def list_licenses(customer_id):
    customer = get_customer(customer_id)
    return {"items": driver_license_repository.search(customer_id=customer["id"])}


def get_license(license_id):
    license_ = driver_license_repository.get(license_id)
    if license_ is None:
        raise LicenseNotFound(details=[{"field": "id", "message": f"No driver license with id {license_id}"}])
    return license_


def submit_license(customer_id, data):
    """Клиент подаёт удостоверение на проверку (US-4); до подтверждения аренда закрыта."""
    customer = get_customer(customer_id)
    return driver_license_repository.add(customer["id"], data)


@transaction.atomic
def review_license(license_id, decision):
    """Оператор подтверждает или отклоняет заявку, которая ещё на проверке."""
    license_ = driver_license_repository.get_for_update(license_id)
    if license_ is None:
        raise LicenseNotFound(details=[{"field": "id", "message": f"No driver license with id {license_id}"}])
    if license_["status"] != LicenseStatus.PENDING:
        raise LicenseAlreadyReviewed(details=[{"field": "status", "message": f"License is {license_['status']}"}])
    return driver_license_repository.update(license_["id"], {
        "status": decision["status"],
        "reject_reason": decision.get("reject_reason"),
        "reviewed_at": _now(),
    })


# --- брони и оплата -------------------------------------------------------
def list_bookings(*, page=1, limit=10, car_id=None, customer_id=None, status=None):
    items, total = booking_repository.search(
        car_id=car_id, customer_id=customer_id, status=status, offset=(page - 1) * limit, limit=limit
    )
    return _page(items, total, page, limit)


def get_booking(booking_id):
    booking = booking_repository.get(booking_id)
    if booking is None:
        raise BookingNotFound(details=[{"field": "id", "message": f"No booking with id {booking_id}"}])
    return booking


def list_payments(booking_id):
    booking = get_booking(booking_id)
    return {"items": payment_repository.search(booking_id=booking["id"])}


def _ensure_can_rent(customer):
    if customer["status"] != CustomerStatus.ACTIVE:
        raise CustomerCannotRent(details=[{"field": "customer_phone", "message": "Customer is blocked"}])
    if not customer["has_valid_license"]:
        raise CustomerCannotRent(
            details=[{"field": "customer_phone", "message": "Driver license is not verified or expired"}]
        )
    if booking_repository.count(customer_id=customer["id"], status=BookingStatus.ACTIVE):
        raise CustomerHasActiveBooking(
            details=[{"field": "customer_phone", "message": "Finish the current booking first"}]
        )


@transaction.atomic
def create_booking(data):
    """Бронирование: запись в bookings, смена статуса машины и запись в журнал — одна транзакция.

    Шаги: 1) заблокировать строку машины и проверить, что она свободна;
    2) проверить, что клиент допущен к аренде (подтверждённое ВУ, нет блокировки);
    3) создать бронь с зафиксированным тарифом; 4) перевести машину в booked
    и записать смену статуса в car_status_history.
    Ошибка на любом шаге откатывает все изменения: не бывает ни брони без
    занятой машины, ни занятой машины без брони.
    """
    car = car_repository.get_for_update(data["car_id"])
    if car is None:
        raise CarNotFound(details=[{"field": "car_id", "message": f"No car with id {data['car_id']}"}])
    if car["status"] != CarStatus.FREE:
        raise CarNotAvailable(details=[{"field": "car_id", "message": f"Car status is {car['status']}"}])

    customer = customer_repository.find_by_phone(data["customer_phone"])
    if customer is None:
        raise CustomerNotFound(
            details=[{"field": "customer_phone", "message": f"No customer with phone {data['customer_phone']}"}]
        )
    _ensure_can_rent(customer)

    booking = booking_repository.add(
        car_id=car["id"],
        customer_id=customer["id"],
        price_per_minute=car["price_per_minute"],
        started_at=_now(),
    )
    car_repository.update(car["id"], {"status": CarStatus.BOOKED})
    _log_status(car["id"], car["status"], CarStatus.BOOKED, booking_id=booking["id"])
    return booking


def charge(customer_id, amount):
    """Заглушка платёжного провайдера: списание всегда успешно, возвращает идентификатор операции."""
    return f"stub-{uuid.uuid4().hex[:16]}"


@transaction.atomic
def close_booking(booking_id, status):
    """Завершение или отмена брони — одна транзакция над четырьмя таблицами.

    Шаги: 1) заблокировать бронь; 2) закрыть её и посчитать стоимость;
    3) при завершении — списать деньги и записать платёж; 4) освободить
    машину и записать смену статуса в журнал.
    """
    booking = booking_repository.get_for_update(booking_id)
    if booking is None:
        raise BookingNotFound(details=[{"field": "id", "message": f"No booking with id {booking_id}"}])
    if booking["status"] != BookingStatus.ACTIVE:
        raise BookingAlreadyClosed(details=[{"field": "status", "message": f"Booking is {booking['status']}"}])

    finished_at = _now()
    total_cost = None
    if status == BookingStatus.FINISHED:
        started_at = datetime.fromisoformat(booking["started_at"])
        minutes = max(1, math.ceil((finished_at - started_at).total_seconds() / 60))
        total_cost = booking["price_per_minute"] * minutes

    closed = booking_repository.update(
        booking["id"], {"status": status, "finished_at": finished_at, "total_cost": total_cost}
    )
    if total_cost:
        payment_repository.add(
            booking_id=booking["id"], amount=total_cost, status=PaymentStatus.PAID,
            provider_payment_id=charge(booking["customer_id"], total_cost), paid_at=finished_at,
        )
    car = car_repository.get(booking["car_id"])
    car_repository.update(booking["car_id"], {"status": CarStatus.FREE})
    _log_status(booking["car_id"], car["status"], CarStatus.FREE, booking_id=booking["id"])
    return closed
