"""Проверка живости сервиса: GET /health отвечает без токена и без обращения к бизнес-логике."""
from tests.helpers import ApiTestCase


class HealthTests(ApiTestCase):
    """GET /health отвечает без токена."""

    def test_health(self):
        for path in ("/health", "/health/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["status"], "ok")
                self.assertEqual(response.json()["service"], "booking-service")
