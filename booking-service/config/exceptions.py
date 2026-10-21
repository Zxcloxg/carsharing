"""Единый формат ошибок всего API.

Любая ошибка, отданная наружу, имеет одинаковую форму:

    {"error": {"code": "machine_readable_code", "message": "Human readable text", "details": [...]}}

`code` — стабильный машиночитаемый код, по которому клиент принимает решение
(например, `token_expired` -> обновить токен, `validation_error` -> подсветить
поля формы). `message` — текст для человека. `details` — список уточнений
(пустой, если уточнять нечего); для ошибок валидации в нём лежат объекты
вида {"field": ..., "message": ...}.

Здесь же — глобальный обработчик DRF: необработанное исключение логируется
со стектрейсом на сервере, а клиент получает 500 в том же формате, без
внутренних подробностей.
"""
import logging

from rest_framework import exceptions, status
from rest_framework.response import Response

logger = logging.getLogger(__name__)

INTERNAL_ERROR_MESSAGE = "Internal server error"


class DomainError(Exception):
    """Ошибка бизнес-логики, которую слой маршрутов превращает в HTTP-ответ."""

    status_code = status.HTTP_400_BAD_REQUEST
    code = "domain_error"
    message = "Domain error"

    def __init__(self, message=None, details=None):
        super().__init__(message or self.message)
        self.message = message or self.message
        self.details = details or []


def error_body(code, message, details=None):
    return {"error": {"code": code, "message": message, "details": details or []}}


def error_response(code, message, details=None, status_code=status.HTTP_400_BAD_REQUEST):
    return Response(error_body(code, message, details), status=status_code)


def _flatten(errors, prefix=""):
    """DRF отдаёт ошибки валидации вложенным словарём — разворачиваем в плоский список."""
    details = []
    if isinstance(errors, dict):
        for field, value in errors.items():
            details.extend(_flatten(value, f"{prefix}.{field}" if prefix else str(field)))
    elif isinstance(errors, list):
        for item in errors:
            details.extend(_flatten(item, prefix))
    else:
        details.append({"field": prefix or "non_field_errors", "message": str(errors)})
    return details


def _code_of(exc):
    """Код ошибки DRF: явно переданный при raise (AuthenticationFailed(code=...)) или код класса."""
    code = getattr(exc.detail, "code", None)
    return code if isinstance(code, str) else exc.default_code


def api_exception_handler(exc, context):
    """Приводит все исключения — доменные, DRF и непредвиденные — к единому формату."""
    # Импорт внутри функции: модуль подключается из классов аутентификации, а
    # rest_framework.views на уровне модуля замкнул бы цикл импортов с настройками DRF.
    from rest_framework.views import exception_handler as drf_exception_handler
    from rest_framework.views import set_rollback

    if isinstance(exc, DomainError):
        set_rollback()
        response = error_response(exc.code, exc.message, exc.details, exc.status_code)
        if exc.status_code == status.HTTP_401_UNAUTHORIZED:
            response["WWW-Authenticate"] = 'Bearer realm="api"'
        return response

    response = drf_exception_handler(exc, context)
    if response is None:
        # Необработанное исключение: подробности — только в лог сервера.
        view = context.get("view")
        logger.exception("Unhandled error in %s", type(view).__name__ if view else "API view", exc_info=exc)
        set_rollback()
        return error_response(
            "internal_error", INTERNAL_ERROR_MESSAGE, status_code=status.HTTP_500_INTERNAL_SERVER_ERROR
        )

    if isinstance(exc, exceptions.ValidationError):
        code, message, details = "validation_error", "Validation failed", _flatten(exc.detail)
    else:
        code, message, details = _code_of(exc), str(exc.detail), getattr(exc, "details", [])
    response.data = error_body(code, message, details)
    return response
