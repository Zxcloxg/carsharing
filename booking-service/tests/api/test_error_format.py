"""Единый формат ошибок для всех кодов ответа, включая 500 без внутренних подробностей (ПР4)."""
from unittest import mock

from django.test import override_settings
from django.urls import path

from accounts.models import Role
from config import urls as project_urls
from fleet import services as fleet_services
from tests.helpers import ME, VALID_CAR, ApiTestCase, authenticate, make_account

def _broken_plain_view(request):
    raise RuntimeError("database password is hunter2")


# Маршрут вне DRF под /api/ — проверка middleware, который отдаёт JSON и на такие сбои.
urlpatterns = [path("api/v1/_broken", _broken_plain_view), *project_urls.urlpatterns]


@override_settings(ROOT_URLCONF=__name__)
class ErrorFormatTests(ApiTestCase):
    """Каждый код ответа 400–500 приходит в едином формате {"error": {...}}."""

    def setUp(self):
        super().setUp()
        authenticate(self.client, make_account(Role.ADMIN))

    def test_400_validation(self):
        error = self.assertError(self.client.post("/api/v1/cars", {}, format="json"), 400, "validation_error")
        self.assertIn({"field": "plate", "message": "This field is required."}, error["details"])

    def test_400_malformed_json(self):
        response = self.client.post("/api/v1/cars", "{bad json", content_type="application/json")
        self.assertError(response, 400, "parse_error")

    def test_401(self):
        self.client.credentials()
        self.assertError(self.client.get(ME), 401, "not_authenticated")

    def test_403(self):
        authenticate(self.client, make_account(Role.CUSTOMER))
        self.assertError(self.client.get("/api/v1/users"), 403, "permission_denied")

    def test_404_domain(self):
        self.assertError(self.client.get("/api/v1/cars/unknown"), 404, "car_not_found")

    def test_404_unknown_url(self):
        self.assertError(self.client.get("/api/v1/no-such-resource"), 404, "not_found")

    def test_405(self):
        self.assertError(self.client.put("/api/v1/cars", {}, format="json"), 405, "method_not_allowed")

    def test_409(self):
        self.client.post("/api/v1/cars", VALID_CAR, format="json")
        self.assertError(self.client.post("/api/v1/cars", VALID_CAR, format="json"), 409, "plate_already_used")

    def test_415(self):
        response = self.client.post("/api/v1/cars", "plate=1", content_type="text/plain")
        self.assertError(response, 415, "unsupported_media_type")

    def test_500_in_api_view_hides_details(self):
        with mock.patch.object(fleet_services, "list_cars", side_effect=RuntimeError("database password is hunter2")), \
                self.assertLogs("config.exceptions", "ERROR"):
            response = self.client.get("/api/v1/cars")
        self.assertError(response, 500, "internal_error")
        self.assertNotIn(b"hunter2", response.content)
        self.assertNotIn(b"Traceback", response.content)

    def test_500_outside_drf_also_json(self):
        with self.assertLogs("config.middleware", "ERROR"):
            response = self.client.get("/api/v1/_broken")
        self.assertError(response, 500, "internal_error")
        self.assertNotIn(b"hunter2", response.content)
