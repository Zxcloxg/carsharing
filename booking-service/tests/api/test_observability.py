"""Наблюдаемость (ПР7): request_id, строка лога на запрос, JSON-формат логов, метрики /metrics."""
import json
import logging
from unittest import mock

from django.db import OperationalError
from django.test import SimpleTestCase, override_settings

from config.observability import METRICS, JsonFormatter, TextFormatter, request_id_var, route_of, task_id_var
from fleet.models import Payment
from tests.helpers import VALID_CAR, ApiTestCase, StaffApiTestCase


def record(message="hello", **extra):
    rec = logging.LogRecord("fleet.payments", logging.WARNING, __file__, 1, message, None, None)
    for key, value in extra.items():
        setattr(rec, key, value)
    return rec


class RequestIdTests(ApiTestCase):
    """Каждый ответ несёт X-Request-ID; этот же id — в строке лога запроса."""

    def test_generated_when_missing(self):
        with self.assertLogs("config.requests", "INFO") as logs:
            response = self.client.get("/api/v1/cars")
        request_id = response["X-Request-ID"]
        self.assertRegex(request_id, r"^[0-9a-f]{32}$")
        entry = logs.records[0]
        self.assertEqual(entry.request_id, request_id)
        self.assertEqual((entry.method, entry.route, entry.status), ("GET", "/api/v1/cars", 200))
        self.assertGreaterEqual(entry.duration_ms, 0)

    def test_incoming_id_from_gateway_is_kept(self):
        with self.assertLogs("config.requests", "INFO"):
            response = self.client.get("/api/v1/cars", HTTP_X_REQUEST_ID="gw-42.abc")
        self.assertEqual(response["X-Request-ID"], "gw-42.abc")

    def test_malicious_incoming_id_is_replaced(self):
        """Заголовок с переводом строки или мусором не попадает в логи как есть (подделка записей лога)."""
        with self.assertLogs("config.requests", "INFO"):
            response = self.client.get("/api/v1/cars", HTTP_X_REQUEST_ID="x\nlevel=ERROR")
        self.assertRegex(response["X-Request-ID"], r"^[0-9a-f]{32}$")

    def test_route_template_instead_of_concrete_id(self):
        with self.assertLogs("config.requests", "INFO") as logs:
            self.client.get("/api/v1/cars/00000000-0000-4000-8000-000000000000")
        self.assertEqual(logs.records[0].route, "/api/v1/cars/{car_id}")
        self.assertEqual(logs.records[0].status, 404)

    def test_health_checks_are_not_logged_at_info(self):
        with self.assertLogs("config.requests", "DEBUG") as logs:
            self.client.get("/health")
        self.assertEqual(logs.records[0].levelno, logging.DEBUG)

    def test_context_is_cleared_after_request(self):
        with self.assertLogs("config.requests", "INFO"):
            self.client.get("/api/v1/cars")
        self.assertIsNone(request_id_var.get())


class FormatterTests(SimpleTestCase):
    def test_json_line_with_context_and_extra_fields(self):
        token = request_id_var.set("req-1")
        try:
            line = JsonFormatter().format(record("Платёж %s", status=503, args_unused=None))
        finally:
            request_id_var.reset(token)
        entry = json.loads(line)
        self.assertEqual(entry["level"], "WARNING")
        self.assertEqual(entry["logger"], "fleet.payments")
        self.assertEqual(entry["message"], "Платёж %s")
        self.assertEqual(entry["request_id"], "req-1")
        self.assertEqual(entry["status"], 503)
        self.assertRegex(entry["time"], r"^\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d\.\d{3}\+00:00$")
        self.assertNotIn("task_id", entry)

    def test_task_id_and_exception(self):
        token = task_id_var.set("task-9")
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            rec = record("failed")
            rec.exc_info = sys.exc_info()
            entry = json.loads(JsonFormatter().format(rec))
        finally:
            task_id_var.reset(token)
        self.assertEqual(entry["task_id"], "task-9")
        self.assertIn("ValueError: boom", entry["exc_info"])

    def test_text_format_appends_context(self):
        formatter = TextFormatter("{levelname} {name}: {message}", style="{")
        self.assertEqual(formatter.format(record("hi", request_id="r1", task_id=None)), "WARNING fleet.payments: hi [r1]")
        self.assertEqual(formatter.format(record("hi")), "WARNING fleet.payments: hi")

    def test_route_of_unmatched_request(self):
        self.assertEqual(route_of(mock.Mock(resolver_match=None)), "unmatched")


class MetricsTests(StaffApiTestCase):
    """GET /metrics — формат Prometheus: запросы, время ответа, очередь и платежи из базы."""

    def setUp(self):
        super().setUp()
        METRICS.reset()

    def test_counts_requests_by_route_and_status(self):
        self.client.post("/api/v1/cars", VALID_CAR, format="json")
        self.client.get("/api/v1/cars/00000000-0000-4000-8000-000000000000")
        text = self.client.get("/metrics").content.decode()
        self.assertIn('http_requests_total{method="POST",route="/api/v1/cars",status="201"} 1', text)
        self.assertIn('http_requests_total{method="GET",route="/api/v1/cars/{car_id}",status="404"} 1', text)
        self.assertIn('http_request_duration_seconds_count{route="/api/v1/cars"} 1', text)
        self.assertIn('http_request_duration_seconds_bucket{route="/api/v1/cars",le="+Inf"} 1', text)
        self.assertIn("# TYPE http_requests_total counter", text)
        self.assertIn("database_up 1", text)
        self.assertIn('background_tasks{status="READY"} 0', text)

    def test_business_gauges(self):
        car = self.client.post("/api/v1/cars", VALID_CAR, format="json").data
        from tests.helpers import make_customer

        make_customer()
        booking = self.client.post("/api/v1/bookings", {"car_id": car["id"], "customer_phone": "+77011234567"},
                                   format="json").data
        self.client.patch(f"/api/v1/bookings/{booking['id']}", {"status": "finished"}, format="json")
        text = self.client.get("/metrics").content.decode()
        self.assertEqual(Payment.objects.count(), 1)
        self.assertIn('payments{status="pending"} 1', text)
        self.assertIn('background_tasks{status="READY"} 1', text)

    def test_content_type(self):
        response = self.client.get("/metrics")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response["Content-Type"].startswith("text/plain; version=0.0.4"))

    @override_settings(METRICS_TOKEN="scrape-secret")
    def test_token_required_when_configured(self):
        self.client.credentials()
        response = self.client.get("/metrics")
        self.assertEqual(response.status_code, 401)
        self.assertEqual(response["WWW-Authenticate"], 'Bearer realm="metrics"')
        self.assertEqual(self.client.get("/metrics", HTTP_AUTHORIZATION="Bearer wrong").status_code, 401)
        self.assertEqual(self.client.get("/metrics", HTTP_AUTHORIZATION="Bearer scrape-secret").status_code, 200)

    def test_database_down_is_reported_as_gauge(self):
        with mock.patch("fleet.models.Payment.objects.values_list", side_effect=OperationalError("down")), \
                mock.patch("health.views.connection.close"):
            text = self.client.get("/metrics").content.decode()
        self.assertIn("database_up 0", text)
        self.assertIn("http_requests_total", text)
