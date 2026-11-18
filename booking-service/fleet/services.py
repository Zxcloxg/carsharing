"""Бизнес-логика каршеринга.

Слой между маршрутом и хранилищем: правила предметной области (уникальность
госномера, допуск клиента к аренде, доступность машины, расчёт стоимости,
пагинация) и границы транзакций живут здесь и ничего не знают ни об HTTP,
ни о том, как именно данные лежат в базе.
"""
import logging
from datetime import datetime, timezone

from django.core.mail import send_mail
from django.db import transaction

from jobs import services as job_services
from jobs.retry import PermanentTaskError

from . import domain, payment_gateway
from .errors import (
    BookingAlreadyClosed,
    BookingNotFound,
    BookingNotPayable,
    CarNotAvailable,
    CarNotFound,
    CustomerNotFound,
    LicenseAlreadyReviewed,
    LicenseNotFound,
    PaymentAlreadyPaid,
    PaymentInProgress,
    PaymentNotFound,
    PlateAlreadyUsed,
)
from .models import BookingStatus, CarStatus, LicenseStatus, PaymentStatus
from .repositories import (
    booking_repository,
    car_model_repository,
    car_repository,
    car_status_history_repository,
    customer_repository,
    driver_license_repository,
    payment_repository,
)

logger = logging.getLogger("fleet.payments")


class PaymentMissing(PermanentTaskError):
    """Платёж для задачи списания не найден — повторять нечего."""


def _now():
    return datetime.now(timezone.utc)


# --- машины ---------------------------------------------------------------
def list_cars(*, page=1, limit=10, status=None, brand=None, max_price_per_minute=None):
    """Отфильтрованный и постранично ограниченный список машин."""
    items, total = car_repository.search(
        status=status, brand=brand, max_price_per_minute=max_price_per_minute,
        offset=domain.page_offset(page, limit), limit=limit,
    )
    return domain.page_envelope(items, total, page, limit)


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
    items, total = customer_repository.search(status=status, offset=domain.page_offset(page, limit), limit=limit)
    return domain.page_envelope(items, total, page, limit)


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
        car_id=car_id, customer_id=customer_id, status=status, offset=domain.page_offset(page, limit), limit=limit
    )
    return domain.page_envelope(items, total, page, limit)


def get_booking(booking_id):
    booking = booking_repository.get(booking_id)
    if booking is None:
        raise BookingNotFound(details=[{"field": "id", "message": f"No booking with id {booking_id}"}])
    return booking


def list_payments(booking_id):
    booking = get_booking(booking_id)
    return {"items": payment_repository.search(booking_id=booking["id"])}


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
    has_active_booking = booking_repository.count(customer_id=customer["id"], status=BookingStatus.ACTIVE) > 0
    domain.ensure_can_rent(customer, has_active_booking)

    booking = booking_repository.add(
        car_id=car["id"],
        customer_id=customer["id"],
        price_per_minute=car["price_per_minute"],
        started_at=_now(),
    )
    car_repository.update(car["id"], {"status": CarStatus.BOOKED})
    _log_status(car["id"], car["status"], CarStatus.BOOKED, booking_id=booking["id"])
    return booking


@transaction.atomic
def close_booking(booking_id, status):
    """Завершение или отмена брони — одна транзакция над пятью таблицами.

    Шаги: 1) заблокировать бронь; 2) закрыть её и посчитать стоимость;
    3) при завершении — создать платёж в статусе pending и поставить в очередь
    задачу списания (ПР6: сам вызов провайдера идёт в фоне, ответ клиенту не ждёт
    банк); 4) освободить машину и записать смену статуса в журнал.
    Задача — строка background_tasks в этой же транзакции: при откате не
    останется ни платежа, ни задачи, а обработчик увидит задачу только вместе с платежом.
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
        total_cost = domain.rental_cost(booking["price_per_minute"], started_at, finished_at)

    closed = booking_repository.update(
        booking["id"], {"status": status, "finished_at": finished_at, "total_cost": total_cost}
    )
    payment = _start_payment(booking["id"], total_cost) if total_cost else None
    car = car_repository.get(booking["car_id"])
    car_repository.update(booking["car_id"], {"status": CarStatus.FREE})
    _log_status(booking["car_id"], car["status"], CarStatus.FREE, booking_id=booking["id"])
    return {**closed, "payment": payment}


# --- платежи (ПР6: списание в фоне) -----------------------------------------
def _start_payment(booking_id, amount):
    """Платёж pending и задача его списания — внутри транзакции вызывающего."""
    from .tasks import charge_payment  # задачи импортируют сервисы — импорт здесь разрывает цикл

    payment = payment_repository.add(booking_id=booking_id, amount=amount, status=PaymentStatus.PENDING)
    task = charge_payment.enqueue(payment_id=payment["id"])
    return payment_repository.update(payment["id"], {"task_id": task.id})


def get_payment(payment_id):
    payment = payment_repository.get(payment_id)
    if payment is None:
        raise PaymentNotFound(details=[{"field": "id", "message": f"No payment with id {payment_id}"}])
    return payment


def payment_with_task(payment):
    """Платёж и статус его фоновой задачи — для ответа клиенту."""
    task = job_services.find_task(payment["task_id"])
    summary = None
    if task:
        summary = {key: task[key] for key in (
            "id", "status", "attempts", "max_attempts", "next_attempt_at", "last_error", "finished_at",
        )}
    return {**payment, "task": summary}


@transaction.atomic
def retry_payment(booking_id):
    """Повторная оплата завершённой поездки, если предыдущее списание не прошло.

    Новая попытка — новый платёж (неуспешные остаются в истории) и новая задача.
    Нельзя, если поездка не завершена, уже оплачена или списание ещё идёт.
    """
    booking = booking_repository.get_for_update(booking_id)
    if booking is None:
        raise BookingNotFound(details=[{"field": "id", "message": f"No booking with id {booking_id}"}])
    if booking["status"] != BookingStatus.FINISHED or not booking["total_cost"]:
        raise BookingNotPayable(details=[{"field": "status", "message": f"Booking is {booking['status']}"}])
    if payment_repository.count(booking_id=booking["id"], status=PaymentStatus.PAID):
        raise PaymentAlreadyPaid(details=[{"field": "booking_id", "message": "Booking has a paid payment"}])
    if payment_repository.count(booking_id=booking["id"], status=PaymentStatus.PENDING):
        raise PaymentInProgress(details=[{"field": "booking_id", "message": "Wait for the current payment to finish"}])
    payment = _start_payment(booking["id"], booking["total_cost"])
    logger.info("Бронь %s: повторная оплата, новый платёж %s", booking["id"][:8], payment["id"][:8])
    return payment


def process_payment(payment_id):
    """Тело фоновой задачи списания: вызвать провайдера и записать результат.

    Идемпотентно: платёж, который уже не pending (списан в прошлой попытке или
    закрыт как сбойный), повторно не списывается.
    """
    payment = payment_repository.get(payment_id)
    if payment is None:
        raise PaymentMissing(f"Payment {payment_id} does not exist")
    if payment["status"] != PaymentStatus.PENDING:
        logger.info("Платёж %s уже в статусе %s — списание не требуется", payment_id[:8], payment["status"])
        return {"payment_id": payment_id, "status": str(payment["status"])}
    booking = booking_repository.get(payment["booking_id"])
    provider_payment_id = payment_gateway.charge(
        payment_id=payment_id, amount=payment["amount"], customer_id=booking["customer_id"],
        description=f"Аренда {booking['id'][:8]}",
    )
    paid = _mark_paid(payment_id, provider_payment_id)
    return {"payment_id": payment_id, "status": str(paid["status"]), "provider_payment_id": provider_payment_id}


@transaction.atomic
def _mark_paid(payment_id, provider_payment_id):
    from .tasks import send_receipt

    payment = payment_repository.get_for_update(payment_id)
    if payment["status"] != PaymentStatus.PENDING:
        return payment
    paid = payment_repository.update(payment_id, {
        "status": PaymentStatus.PAID, "provider_payment_id": provider_payment_id, "paid_at": _now(),
    })
    logger.info("Платёж %s оплачен: %s ₸, операция %s", payment_id[:8], paid["amount"], provider_payment_id)
    send_receipt.enqueue(payment_id=payment_id)  # чек уйдёт, только если списание зафиксировано
    return paid


@transaction.atomic
def fail_payment(payment_id, reason):
    """Списание не удалось окончательно: платёж failed с причиной; клиент может оплатить заново."""
    payment = payment_repository.get_for_update(payment_id)
    if payment is None or payment["status"] != PaymentStatus.PENDING:
        return payment
    failed = payment_repository.update(payment_id, {
        "status": PaymentStatus.FAILED, "failure_reason": reason or "Unknown error",
    })
    logger.error(
        "Платёж %s не прошёл: %s. Повторить оплату: POST /api/v1/bookings/%s/payments",
        payment_id[:8], failed["failure_reason"], payment["booking_id"],
    )
    return failed


def send_receipt(payment_id):
    """Тело фоновой задачи: электронный чек клиенту после успешного списания."""
    payment = get_payment(payment_id)
    booking = booking_repository.get(payment["booking_id"])
    customer = customer_repository.get(booking["customer_id"])
    if not customer.get("email"):
        logger.info("Чек по платежу %s не отправлен: у клиента нет e-mail", payment_id[:8])
        return {"sent": False}
    send_mail(
        subject=f"Каршеринг: чек за поездку {booking['id'][:8]}",
        message=(
            f"Поездка завершена {booking['finished_at']}.\n"
            f"Списано: {payment['amount']} ₸ (операция {payment['provider_payment_id']}).\n"
            "Спасибо, что выбрали нас!"
        ),
        from_email=None,
        recipient_list=[customer["email"]],
    )
    logger.info("Чек по платежу %s отправлен на %s", payment_id[:8], customer["email"])
    return {"sent": True, "to": customer["email"]}
