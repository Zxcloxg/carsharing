"""Проверка токена — первый этап обработки каждого запроса к API.

Подключён глобально (REST_FRAMEWORK.DEFAULT_AUTHENTICATION_CLASSES), поэтому
работает как middleware для всех маршрутов DRF: разбирает заголовок
`Authorization: Bearer <token>` и кладёт пользователя в request.user.

* Заголовка нет -> request.user = None (аноним). Что анониму можно, решают
  декораторы маршрута (accounts/permissions.py).
* Заголовок есть, но токен битый, просрочен или пользователь отключён ->
  сразу 401, даже на публичном маршруте: клиент должен узнать, что его
  токен больше не работает.
"""
from dataclasses import dataclass

from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from .repository import UserRepository
from .tokens import TOKEN_TYPE, decode_access_token

users = UserRepository()


@dataclass(frozen=True)
class CurrentUser:
    """Пользователь текущего запроса. Роль и активность — из базы, а не из токена."""

    id: str
    email: str
    role: str
    customer_id: str | None

    is_authenticated = True


class JWTAuthentication(BaseAuthentication):
    def authenticate(self, request):
        header = request.META.get("HTTP_AUTHORIZATION", "")
        if not header:
            return None
        scheme, _, token = header.partition(" ")
        if scheme.lower() != TOKEN_TYPE.lower() or not token.strip():
            raise AuthenticationFailed("Authorization header must be 'Bearer <token>'", code="invalid_token")

        payload = decode_access_token(token.strip())
        user = users.get(payload["sub"])
        if user is None:
            raise AuthenticationFailed("Access token is invalid", code="invalid_token")
        if not user["is_active"]:
            raise AuthenticationFailed("Account is disabled", code="account_disabled")
        return CurrentUser(user["id"], user["email"], user["role"], user["customer_id"]), payload

    def authenticate_header(self, request):
        # Наличие заголовка WWW-Authenticate заставляет DRF отвечать 401, а не 403.
        return f'{TOKEN_TYPE} realm="api"'
