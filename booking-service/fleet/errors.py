"""Единый формат ошибок API.

Любая ошибка, отданная наружу, имеет одинаковую форму:

    {"error": "human readable message", "details": [...]}

`details` — список уточнений (пустой, если уточнять нечего). Для ошибок
валидации в нём лежат объекты вида {"field": ..., "message": ...}.
"""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import exception_handler as drf_exception_handler


class DomainError(Exception):
    """Ошибка бизнес-логики, которую слой маршрутов превращает в HTTP-ответ."""

    status_code = status.HTTP_400_BAD_REQUEST
    message = "Domain error"

    def __init__(self, message=None, details=None):
        super().__init__(message or self.message)
        self.message = message or self.message
        self.details = details or []


class CarNotFound(DomainError):
    status_code = status.HTTP_404_NOT_FOUND
    message = "Car not found"


class PlateAlreadyUsed(DomainError):
    status_code = status.HTTP_409_CONFLICT
    message = "Car with this plate already exists"


def error_response(message, details=None, status_code=status.HTTP_400_BAD_REQUEST):
    return Response({"error": message, "details": details or []}, status=status_code)


def _flatten(errors, prefix=""):
    """DRF отдаёт ошибки вложенным словарём — разворачиваем в плоский список."""
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


def api_exception_handler(exc, context):
    """Приводит все исключения DRF и домена к единому формату ошибок."""
    if isinstance(exc, DomainError):
        return error_response(exc.message, exc.details, exc.status_code)

    response = drf_exception_handler(exc, context)
    if response is None:
        return None

    data = response.data
    if isinstance(data, dict) and set(data) == {"detail"}:
        message, details = str(data["detail"]), []
    else:
        message, details = "Validation failed", _flatten(data)
    response.data = {"error": message, "details": details}
    return response
