"""Схемы валидации входных данных (DTO).

Валидация выполняется до бизнес-логики: маршрут не пускает внутрь ни одного
некорректного поля. Схемы сознательно не привязаны к моделям Django: контракт
API и структура таблиц могут меняться независимо (хранение — fleet/repositories/).
"""
from datetime import date, datetime, timezone
from decimal import Decimal

from rest_framework import serializers

from . import domain
from .models import PHONE_REGEX, BookingStatus, CarStatus, CustomerStatus, LicenseStatus

CAR_STATUSES = tuple(CarStatus.values)
BOOKING_STATUSES = tuple(BookingStatus.values)
CUSTOMER_STATUSES = tuple(CustomerStatus.values)
MAX_YEAR = domain.max_model_year(datetime.now(timezone.utc).date())


class CarSerializer(serializers.Serializer):
    """Полное тело автомобиля: используется для POST и PUT."""

    plate = serializers.RegexField(
        r"^[A-Z0-9-]{4,10}$",
        help_text="Госномер, 4-10 символов: латиница в верхнем регистре, цифры, дефис",
    )
    brand = serializers.CharField(min_length=2, max_length=50)
    model = serializers.CharField(min_length=1, max_length=50)
    year = serializers.IntegerField(min_value=1990, max_value=MAX_YEAR)
    color = serializers.CharField(min_length=2, max_length=30, required=False, allow_null=True, default=None)
    price_per_minute = serializers.DecimalField(
        max_digits=8, decimal_places=2, min_value=Decimal("0.01"), coerce_to_string=False
    )
    fuel_level = serializers.FloatField(min_value=0, max_value=1, required=False, default=1.0)
    status = serializers.ChoiceField(choices=CAR_STATUSES, required=False, default="free")

    def validate_plate(self, value):
        return domain.normalize_plate(value)


class CarPatchSerializer(CarSerializer):
    """Частичное обновление: те же правила, но все поля необязательны."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field in self.fields.values():
            field.required = False
            field.default = serializers.empty

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("At least one field must be provided")
        return attrs


class CarListQuerySerializer(serializers.Serializer):
    """Параметры запроса списка: пагинация и фильтры."""

    page = serializers.IntegerField(min_value=1, required=False, default=1)
    limit = serializers.IntegerField(min_value=1, max_value=100, required=False, default=10)
    status = serializers.ChoiceField(choices=CAR_STATUSES, required=False)
    brand = serializers.CharField(max_length=50, required=False)
    max_price_per_minute = serializers.DecimalField(
        max_digits=8, decimal_places=2, min_value=Decimal("0.01"), required=False, coerce_to_string=False
    )


class CustomerSerializer(serializers.Serializer):
    """Тело POST /api/v1/customers: регистрация клиента."""

    phone = serializers.RegexField(PHONE_REGEX, help_text="Телефон, 10-15 цифр, можно с +")
    email = serializers.EmailField(max_length=254, required=False, allow_null=True, default=None)
    full_name = serializers.CharField(min_length=2, max_length=150)
    birth_date = serializers.DateField()

    def validate_birth_date(self, value):
        if not domain.is_adult(value, date.today()):
            raise serializers.ValidationError(f"Customer must be at least {domain.MIN_DRIVER_AGE} years old")
        return value

    def validate_email(self, value):
        return value.lower() if value else value


class CustomerPatchSerializer(serializers.Serializer):
    """Тело PATCH /api/v1/customers/{id}: оператор блокирует или разблокирует клиента."""

    status = serializers.ChoiceField(choices=CUSTOMER_STATUSES)


class CustomerListQuerySerializer(serializers.Serializer):
    page = serializers.IntegerField(min_value=1, required=False, default=1)
    limit = serializers.IntegerField(min_value=1, max_value=100, required=False, default=10)
    status = serializers.ChoiceField(choices=CUSTOMER_STATUSES, required=False)


class DriverLicenseSerializer(serializers.Serializer):
    """Тело POST /api/v1/customers/{id}/driver-licenses: данные удостоверения на проверку."""

    number = serializers.RegexField(r"^[A-Za-z0-9]{6,20}$", help_text="Номер удостоверения, 6-20 букв и цифр")
    categories = serializers.RegexField(
        r"^[A-Za-z0-9]{1,3}(,[A-Za-z0-9]{1,3})*$", help_text="Категории через запятую, например B,C"
    )
    issued_on = serializers.DateField()
    expires_on = serializers.DateField()

    def validate_number(self, value):
        return value.upper()

    def validate_categories(self, value):
        if not domain.allows_car_rental(value):
            raise serializers.ValidationError("Category B is required to rent a car")
        return domain.normalize_categories(value)

    def validate(self, attrs):
        problems = domain.license_date_problems(attrs["issued_on"], attrs["expires_on"], date.today())
        if problems:
            raise serializers.ValidationError(problems)
        return attrs


class LicenseReviewSerializer(serializers.Serializer):
    """Тело PATCH /api/v1/driver-licenses/{id}: решение оператора."""

    status = serializers.ChoiceField(choices=(LicenseStatus.APPROVED, LicenseStatus.REJECTED))
    reject_reason = serializers.CharField(min_length=3, max_length=500, required=False)

    def validate(self, attrs):
        if attrs["status"] == LicenseStatus.REJECTED and not attrs.get("reject_reason"):
            raise serializers.ValidationError({"reject_reason": "Required when the license is rejected"})
        if attrs["status"] == LicenseStatus.APPROVED:
            attrs.pop("reject_reason", None)
        return attrs


class BookingCreateSerializer(serializers.Serializer):
    """Тело POST /api/v1/bookings: какую машину бронирует какой клиент."""

    car_id = serializers.UUIDField()
    customer_phone = serializers.RegexField(
        PHONE_REGEX, required=False,
        help_text="Телефон клиента: обязателен для персонала; клиент бронирует на себя и может его не указывать",
    )


class BookingPatchSerializer(serializers.Serializer):
    """Тело PATCH /api/v1/bookings/{id}: завершить или отменить бронь."""

    status = serializers.ChoiceField(choices=(BookingStatus.FINISHED, BookingStatus.CANCELLED))


class BookingListQuerySerializer(serializers.Serializer):
    page = serializers.IntegerField(min_value=1, required=False, default=1)
    limit = serializers.IntegerField(min_value=1, max_value=100, required=False, default=10)
    car_id = serializers.UUIDField(required=False)
    customer_id = serializers.UUIDField(required=False)
    status = serializers.ChoiceField(choices=BOOKING_STATUSES, required=False)
