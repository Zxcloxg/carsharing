"""Авторизация: декораторы маршрутов и проверка владельца записи.

Два этапа проверяются раздельно:

1. require_auth — есть ли валидный токен. Нет -> 401 not_authenticated.
2. require_role — разрешено ли действие роли пользователя. Нет -> 403
   permission_denied. Декоратор сначала выполняет проверку п. 1, поэтому
   анониму на маршрут администратора всегда отвечает 401, а не 403.

ensure_owner_or_staff — проверка на уровне записи: клиент видит и меняет
только свои данные (профиль, ВУ, брони), персонал — любые.
"""
from functools import wraps

from rest_framework.exceptions import NotAuthenticated

from .errors import AccessDenied
from .models import Role

STAFF_ROLES = (Role.OPERATOR, Role.ADMIN)
ALL_ROLES = tuple(Role.values)


def require_auth(view_method):
    @wraps(view_method)
    def wrapper(view, request, *args, **kwargs):
        if not getattr(request.user, "is_authenticated", False):
            raise NotAuthenticated()
        return view_method(view, request, *args, **kwargs)

    return wrapper


def require_role(*roles):
    def decorator(view_method):
        @require_auth
        @wraps(view_method)
        def wrapper(view, request, *args, **kwargs):
            if request.user.role not in roles:
                raise AccessDenied(details=[{
                    "field": "role",
                    "message": f"Allowed roles: {', '.join(roles)}; your role: {request.user.role}",
                }])
            return view_method(view, request, *args, **kwargs)

        return wrapper

    return decorator


def is_staff(user):
    return user.role in STAFF_ROLES


def ensure_owner_or_staff(user, customer_id):
    if is_staff(user) or (user.customer_id and user.customer_id == str(customer_id)):
        return
    raise AccessDenied(
        "You can access only your own data",
        details=[{"field": "customer_id", "message": "Record belongs to another customer"}],
    )
