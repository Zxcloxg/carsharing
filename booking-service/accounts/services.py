"""Бизнес-логика учётных записей: регистрация, вход, управление ролями."""
from datetime import datetime, timezone

from django.contrib.auth.hashers import check_password, make_password
from django.db import transaction

from fleet import services as fleet_services

from .errors import (
    AccountDisabled,
    CannotChangeOwnAccount,
    CustomerProfileExists,
    InvalidCredentials,
    UserNotFound,
)
from .models import Role
from .repository import UserRepository
from .tokens import TOKEN_TYPE, expires_in_seconds, issue_access_token

users = UserRepository()


def register(email, password):
    """Самостоятельная регистрация всегда даёт роль customer; персонал заводит администратор."""
    return users.add(email=email, raw_password=password, role=Role.CUSTOMER)


def create_user(email, password, role):
    """Создание учётной записи с любой ролью — для команды create_user и демо-данных."""
    return users.add(email=email, raw_password=password, role=role)


def login(email, password):
    """Проверяет пароль и выдаёт access-токен."""
    user = users.find_credentials(email)
    if user is None:
        # Хэшируем «вхолостую», чтобы ответ для несуществующего email занимал
        # столько же времени, сколько для существующего: по таймингу нельзя
        # перебором выяснить, какие адреса зарегистрированы.
        make_password(password)
        raise InvalidCredentials()
    if not check_password(password, user.pop("password_hash"), setter=lambda raw: users.set_password(user["id"], raw)):
        raise InvalidCredentials()
    if not user["is_active"]:
        raise AccountDisabled()

    user = users.update(user["id"], {"last_login_at": datetime.now(timezone.utc)})
    return {
        "access_token": issue_access_token(user),
        "token_type": TOKEN_TYPE,
        "expires_in": expires_in_seconds(),
        "user": user,
    }


def get_user(user_id):
    user = users.get(user_id)
    if user is None:
        raise UserNotFound(details=[{"field": "id", "message": f"No user with id {user_id}"}])
    return user


def list_users(*, page=1, limit=10, role=None):
    items, total = users.search(role=role, offset=(page - 1) * limit, limit=limit)
    return {
        "items": items, "page": page, "limit": limit, "total": total,
        "pages": (total + limit - 1) // limit if total else 0,
    }


def update_user(actor, user_id, changes):
    """Администратор меняет роль или блокирует учётную запись (но не свою — чтобы не остаться без админа)."""
    user = get_user(user_id)
    if user["id"] == actor.id:
        raise CannotChangeOwnAccount(details=[{"field": "id", "message": "Ask another administrator"}])
    return users.update(user["id"], changes)


@transaction.atomic
def create_customer_profile(actor, data):
    """Клиент заполняет профиль арендатора; профиль сразу привязывается к его учётной записи.

    Строка пользователя блокируется, чтобы два параллельных запроса не создали
    два профиля для одного аккаунта.
    """
    account = users.get_for_update(actor.id)
    if account["customer_id"]:
        raise CustomerProfileExists(details=[{"field": "customer_id", "message": account["customer_id"]}])
    data = {**data, "email": data.get("email") or account["email"]}
    customer = fleet_services.create_customer(data)
    users.update(account["id"], {"customer_id": customer["id"]})
    return customer
