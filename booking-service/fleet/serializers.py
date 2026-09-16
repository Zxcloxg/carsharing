"""Схемы валидации входных данных (DTO).

Валидация выполняется до бизнес-логики: маршрут не пускает внутрь ни одного
некорректного поля. Модели Django здесь сознательно не используются — на этом
этапе хранение временное (см. fleet/repository.py).
"""
from datetime import datetime, timezone
from decimal import Decimal

from rest_framework import serializers

CAR_STATUSES = ("free", "booked", "in_rent", "service")
MAX_YEAR = datetime.now(timezone.utc).year + 1


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
        return value.strip().upper()


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
