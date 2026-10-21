"""Схемы валидации запросов аутентификации и управления пользователями."""
from django.contrib.auth import password_validation
from django.core.exceptions import ValidationError as DjangoValidationError
from rest_framework import serializers

from .models import Role, User

ROLES = tuple(Role.values)


class RegisterSerializer(serializers.Serializer):
    """Тело POST /api/v1/auth/register."""

    email = serializers.EmailField(max_length=254)
    # Верхняя граница защищает от DoS: хэширование мегабайтного пароля дорого.
    password = serializers.CharField(min_length=8, max_length=128, write_only=True, trim_whitespace=False)

    def validate_email(self, value):
        return value.strip().lower()

    def validate(self, attrs):
        # Роль не выбирается при регистрации: иначе любой зарегистрировался бы администратором.
        if "role" in self.initial_data:
            raise serializers.ValidationError({"role": "Role cannot be set at registration"})
        try:
            # Валидаторы Django: не слишком похож на email, не из списка частых, не только цифры.
            # Несохранённый User нужен валидатору похожести: он читает поля модели через _meta.
            password_validation.validate_password(attrs["password"], user=User(email=attrs["email"]))
        except DjangoValidationError as exc:
            raise serializers.ValidationError({"password": list(exc.messages)}) from exc
        return attrs


class LoginSerializer(serializers.Serializer):
    """Тело POST /api/v1/auth/login. Правила сложности не проверяются — только наличие полей."""

    email = serializers.EmailField(max_length=254)
    password = serializers.CharField(max_length=128, write_only=True, trim_whitespace=False)

    def validate_email(self, value):
        return value.strip().lower()


class UserPatchSerializer(serializers.Serializer):
    """Тело PATCH /api/v1/users/{id}: администратор меняет роль или блокирует учётную запись."""

    role = serializers.ChoiceField(choices=ROLES, required=False)
    is_active = serializers.BooleanField(required=False)

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError("At least one field must be provided: role, is_active")
        return attrs


class UserListQuerySerializer(serializers.Serializer):
    page = serializers.IntegerField(min_value=1, required=False, default=1)
    limit = serializers.IntegerField(min_value=1, max_value=100, required=False, default=10)
    role = serializers.ChoiceField(choices=ROLES, required=False)
