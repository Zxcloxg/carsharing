"""Проверка состояния (ПР7): /health отражает доступность базы данных, /health/live — только процесс."""
from unittest import mock

from django.db import OperationalError, connection
from django.tasks import TaskResultStatus
from django.utils import timezone

from jobs.models import BackgroundTask
from tests.helpers import ApiTestCase


class HealthTests(ApiTestCase):
    """GET /health — готовность: 200, пока база отвечает, и 503, когда нет."""

    def test_health_ok_with_database_check(self):
        for path in ("/health", "/health/"):
            with self.subTest(path=path):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                body = response.json()
                self.assertEqual(body["status"], "ok")
                self.assertEqual(body["service"], "booking-service")
                self.assertEqual(body["checks"]["database"]["status"], "ok")
                self.assertIsInstance(body["checks"]["database"]["latency_ms"], float)

    def test_health_reports_queue_state(self):
        BackgroundTask.objects.create(task_path="fleet.tasks.charge_payment", max_attempts=3, run_after=timezone.now())
        BackgroundTask.objects.create(task_path="fleet.tasks.charge_payment", max_attempts=3, run_after=timezone.now(),
                                      status=TaskResultStatus.FAILED, finished_at=timezone.now())
        queue = self.client.get("/health").json()["checks"]["queue"]
        self.assertEqual((queue["ready"], queue["failed"], queue["running"]), (1, 1, 0))
        self.assertGreaterEqual(queue["oldest_ready_age_s"], 0)

    def test_database_down_gives_503_without_internal_details(self):
        broken = OperationalError('could not translate host name "db" to address')
        with mock.patch.object(connection, "cursor", side_effect=broken), \
                mock.patch.object(connection, "close"), self.assertLogs("health", "ERROR") as logs:
            response = self.client.get("/health")
        self.assertEqual(response.status_code, 503)
        body = response.json()
        self.assertEqual(body["status"], "unavailable")
        self.assertEqual(body["checks"]["database"],
                         {"status": "error", "error": "OperationalError: database is unreachable"})
        self.assertNotIn("queue", body["checks"])
        self.assertNotIn("db", response.content.decode())  # имя хоста — только в логе сервера
        self.assertIn('could not translate host name "db"', logs.output[0])

    def test_liveness_does_not_touch_database(self):
        with mock.patch.object(connection, "cursor", side_effect=OperationalError("down")):
            response = self.client.get("/health/live")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ok")
