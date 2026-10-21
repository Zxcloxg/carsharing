"""Учётные записи пользователей API.

Отдельная таблица users, а не встроенная auth_user Django: auth_user
обслуживает вход сотрудников в админку (/admin/), а users — вход клиентов и
персонала в публичный REST API по JWT. У API-пользователя ровно те поля,
которые нужны API: email как логин, хэш пароля, роль и связь с профилем
клиента.
"""
import uuid

from django.contrib.auth.hashers import make_password
from django.db import models
from django.db.models import Q
from django.db.models.functions import Lower


class Role(models.TextChoices):
    CUSTOMER = "customer", "Клиент"
    OPERATOR = "operator", "Оператор"
    ADMIN = "admin", "Администратор"


class User(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(max_length=254, db_comment="Логин; хранится в нижнем регистре")
    password_hash = models.CharField(
        max_length=255, db_comment="Хэш пароля в формате <алгоритм>$<параметры>$<соль>$<хэш>; открытый пароль не хранится"
    )
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.CUSTOMER)
    # Профиль арендатора (телефон, ВУ, брони). У персонала профиля нет.
    customer = models.OneToOneField(
        "fleet.Customer", on_delete=models.SET_NULL, null=True, blank=True, related_name="account"
    )
    is_active = models.BooleanField(default=True, db_comment="false — вход запрещён, выданные токены не принимаются")
    created_at = models.DateTimeField(auto_now_add=True, db_comment="Дата регистрации")
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "users"
        db_table_comment = "Учётные записи пользователей API"
        ordering = ["created_at"]
        constraints = [
            # Уникальность без учёта регистра: Ivan@Mail.kz и ivan@mail.kz — один логин.
            models.UniqueConstraint(Lower("email"), name="users_email_lower_uniq"),
            models.CheckConstraint(condition=Q(role__in=Role.values), name="users_role_valid"),
            # Страховка на уровне базы: в колонку может попасть только строка формата хэшера
            # (argon2$..., pbkdf2_sha256$...), а не пароль «как есть».
            models.CheckConstraint(
                condition=Q(password_hash__regex=r"^[a-z0-9_]+\$.+\$.+"), name="users_password_is_hashed"
            ),
        ]

    def __str__(self):
        return f"{self.email} ({self.role})"

    def set_password(self, raw_password):
        """Хэширует пароль первым хэшером из PASSWORD_HASHERS (Argon2id) со случайной солью."""
        self.password_hash = make_password(raw_password)
