"""Выпуск и проверка access-токенов JWT.

Токен подписан HMAC-SHA256 секретом JWT_SECRET из окружения. Полезная
нагрузка: sub — id пользователя, role — роль на момент входа (для клиента,
сервер ей не доверяет и берёт роль из базы), iss — кто выпустил, iat/exp —
время выпуска и истечения.
"""
from datetime import datetime, timezone

import jwt
from django.conf import settings
from rest_framework.exceptions import AuthenticationFailed

TOKEN_TYPE = "Bearer"


def issue_access_token(user, now=None):
    now = now or datetime.now(timezone.utc)
    payload = {
        "sub": user["id"],
        "role": user["role"],
        "iss": settings.JWT_ISSUER,
        "iat": now,
        "exp": now + settings.JWT_ACCESS_TTL,
    }
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def decode_access_token(token):
    """Проверяет подпись, срок действия и издателя; при любой проблеме — 401."""
    try:
        return jwt.decode(
            token,
            settings.JWT_SECRET,
            # Алгоритм задан жёстко: токен с alg=none или чужим алгоритмом не пройдёт.
            algorithms=[settings.JWT_ALGORITHM],
            issuer=settings.JWT_ISSUER,
            options={"require": ["sub", "iat", "exp", "iss"]},
        )
    except jwt.ExpiredSignatureError as exc:
        raise AuthenticationFailed("Access token has expired", code="token_expired") from exc
    except jwt.InvalidTokenError as exc:
        raise AuthenticationFailed("Access token is invalid", code="invalid_token") from exc


def expires_in_seconds():
    return int(settings.JWT_ACCESS_TTL.total_seconds())
