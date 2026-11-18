"""Модульные тесты единого формата ошибок (config/exceptions.py): обработчик вызывается напрямую."""
from django.test import SimpleTestCase
from rest_framework import exceptions

from config.exceptions import _flatten, api_exception_handler
from fleet.errors import CarNotFound, PlateAlreadyUsed


def handle(exc):
    return api_exception_handler(exc, {"view": None})


class FlattenTests(SimpleTestCase):
    """Вложенные ошибки валидации разворачиваются в плоский список {field, message}."""

    def test_nested_errors_become_flat_list(self):
        details = _flatten({"plate": ["Required."], "driver": {"phone": ["Bad format.", "Too short."]}})
        self.assertEqual(details, [
            {"field": "plate", "message": "Required."},
            {"field": "driver.phone", "message": "Bad format."},
            {"field": "driver.phone", "message": "Too short."},
        ])

    def test_error_without_field(self):
        self.assertEqual(_flatten(["At least one field"]), [{"field": "non_field_errors", "message": "At least one field"}])


class HandlerTests(SimpleTestCase):
    """Глобальный обработчик приводит доменные, DRF- и непредвиденные ошибки к единому формату."""

    def test_domain_error(self):
        response = handle(PlateAlreadyUsed(details=[{"field": "plate", "message": "taken"}]))
        self.assertEqual(response.status_code, 409)
        self.assertEqual(response.data, {"error": {
            "code": "plate_already_used", "message": "Car with this plate already exists",
            "details": [{"field": "plate", "message": "taken"}],
        }})

    def test_domain_error_default_details(self):
        self.assertEqual(handle(CarNotFound()).data["error"]["details"], [])

    def test_validation_error(self):
        response = handle(exceptions.ValidationError({"year": ["Must be >= 1990."]}))
        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.data["error"]["code"], "validation_error")
        self.assertEqual(response.data["error"]["details"], [{"field": "year", "message": "Must be >= 1990."}])

    def test_authentication_error_keeps_specific_code(self):
        response = handle(exceptions.AuthenticationFailed("Access token has expired", code="token_expired"))
        self.assertEqual(response.data["error"]["code"], "token_expired")

    def test_throttled(self):
        response = handle(exceptions.Throttled(wait=42))
        self.assertEqual(response.status_code, 429)
        self.assertEqual(response.data["error"]["code"], "throttled")
        self.assertEqual(response["Retry-After"], "42")

    def test_unexpected_error_hides_details(self):
        with self.assertLogs("config.exceptions", "ERROR") as logs:
            response = handle(RuntimeError("password=hunter2 at db.internal:5432"))
        self.assertEqual(response.status_code, 500)
        self.assertEqual(response.data, {"error": {"code": "internal_error", "message": "Internal server error", "details": []}})
        self.assertIn("hunter2", str(logs.records[0].exc_info[1]))  # подробности — только в лог сервера
