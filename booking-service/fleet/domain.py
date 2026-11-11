"""Правила предметной области каршеринга — чистые функции.

Здесь нет ни HTTP, ни базы данных, ни текущего времени «из воздуха»: всё,
от чего зависит результат, передаётся аргументами. Поэтому правила
проверяются модульными тестами за миллисекунды (tests/unit/), а сервисы
(fleet/services.py) и схемы валидации (fleet/serializers.py) только
вызывают их.
"""
import math
from datetime import date
from decimal import ROUND_HALF_UP, Decimal

from .errors import CustomerCannotRent, CustomerHasActiveBooking
from .models import CustomerStatus

MIN_DRIVER_AGE = 18
REQUIRED_LICENSE_CATEGORY = "B"
MONEY_STEP = Decimal("0.01")


# --- пагинация --------------------------------------------------------------
def page_offset(page, limit):
    """Сколько записей пропустить, чтобы получить страницу `page` (нумерация с 1)."""
    if page < 1 or limit < 1:
        raise ValueError("page and limit must be positive")
    return (page - 1) * limit


def page_envelope(items, total, page, limit):
    """Ответ списка: элементы страницы и метаданные для навигации клиента."""
    return {
        "items": items,
        "page": page,
        "limit": limit,
        "total": total,
        "pages": math.ceil(total / limit) if total else 0,
    }


# --- стоимость аренды -------------------------------------------------------
def billable_minutes(started_at, finished_at):
    """Оплачиваемые минуты: каждая начатая минута считается целиком, но не меньше одной.

    Время завершения раньше начала означает сбой часов или порчу данных —
    такую аренду считать нельзя.
    """
    seconds = (finished_at - started_at).total_seconds()
    if seconds < 0:
        raise ValueError("finished_at is earlier than started_at")
    return max(1, math.ceil(seconds / 60))


def rental_cost(price_per_minute, started_at, finished_at):
    """Стоимость аренды в тенге по тарифу, зафиксированному в момент брони."""
    minutes = billable_minutes(started_at, finished_at)
    return (Decimal(price_per_minute) * minutes).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)


# --- клиент и допуск к аренде -----------------------------------------------
def age_on(birth_date, today):
    """Полных лет на дату `today`; родившийся 29 февраля стареет 1 марта."""
    had_birthday = (today.month, today.day) >= (birth_date.month, birth_date.day)
    return today.year - birth_date.year - (0 if had_birthday else 1)


def is_adult(birth_date, today):
    return age_on(birth_date, today) >= MIN_DRIVER_AGE


def ensure_can_rent(customer, has_active_booking):
    """Допуск к аренде: клиент не заблокирован, ВУ подтверждено и действует, нет другой активной аренды."""
    if customer["status"] != CustomerStatus.ACTIVE:
        raise CustomerCannotRent(details=[{"field": "customer_phone", "message": "Customer is blocked"}])
    if not customer["has_valid_license"]:
        raise CustomerCannotRent(
            details=[{"field": "customer_phone", "message": "Driver license is not verified or expired"}]
        )
    if has_active_booking:
        raise CustomerHasActiveBooking(
            details=[{"field": "customer_phone", "message": "Finish the current booking first"}]
        )


# --- водительское удостоверение ---------------------------------------------
def normalize_categories(raw):
    """«b, c » -> «B,C»: верхний регистр, без пробелов, без повторов, порядок сохраняется."""
    seen = []
    for part in raw.upper().replace(" ", "").split(","):
        if part and part not in seen:
            seen.append(part)
    return ",".join(seen)


def allows_car_rental(categories):
    """Нужна именно категория B; «BE» или «C1» её не заменяют."""
    return REQUIRED_LICENSE_CATEGORY in normalize_categories(categories).split(",")


def license_date_problems(issued_on, expires_on, today):
    """Ошибки в датах удостоверения: {поле: сообщение}; пустой словарь — даты в порядке."""
    if issued_on > today:
        return {"issued_on": "Issue date cannot be in the future"}
    if expires_on <= issued_on:
        return {"expires_on": "Must be later than issued_on"}
    if expires_on < today:
        return {"expires_on": "Driver license has expired"}
    return {}


# --- автомобиль -------------------------------------------------------------
def normalize_plate(raw):
    """Госномер хранится в одном виде: без пробелов по краям, в верхнем регистре."""
    return raw.strip().upper()


def max_model_year(today: date):
    """Самый новый допустимый год выпуска: модели следующего года продаются заранее."""
    return today.year + 1
