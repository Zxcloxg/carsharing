"""Фоновое списание за поездку (ПР6): очередь, обработчик, повторы, сбой и API статуса.

Платёжный провайдер подменён (mock), обработчик запускается прямо в тесте —
Worker.run_pending() выполняет всё, что готово в очереди, тем же кодом, что и
python manage.py run_worker. Пауза между повторами в тестах — 0 с, поэтому
все попытки укладываются в один вызов run_pending().
"""
from datetime import timedelta
from io import StringIO
from unittest import mock

from django.core import mail
from django.core.management import call_command
from django.db import IntegrityError, transaction
from django.tasks import TaskResultStatus, default_task_backend
from django.tasks.exceptions import TaskResultDoesNotExist
from django.test import override_settings
from django.utils import timezone

from accounts.models import Role
from fleet import payment_gateway, services
from fleet.models import Customer, Payment
from fleet.payment_gateway import PaymentDeclined, PaymentProviderUnavailable
from fleet.tasks import charge_payment
from jobs.models import BackgroundTask
from jobs.worker import Worker
from tests.helpers import VALID_CAR, ApiTestCase, StaffApiTestCase, authenticate, make_account, make_customer

TASKS_NO_BACKOFF = {
    "default": {
        "BACKEND": "jobs.backends.DatabaseBackend",
        "QUEUES": ["default"],
        "OPTIONS": {"MAX_ATTEMPTS": 3, "RETRY_BACKOFF_SECONDS": 0, "LEASE_SECONDS": 300},
    },
}
UNAVAILABLE = PaymentProviderUnavailable("Provider is unreachable at http://127.0.0.1:8030/v1/charges: refused")


def provider(*outcomes):
    """Подмена провайдера: по очереди возвращает id операции или бросает исключение."""
    results = [o if isinstance(o, Exception) else f"ch_{o}" for o in outcomes]
    return mock.patch.object(payment_gateway, "charge", side_effect=results)


@override_settings(TASKS=TASKS_NO_BACKOFF)
class PaymentQueueTestCase(StaffApiTestCase):
    def setUp(self):
        super().setUp()
        self.car = self.client.post("/api/v1/cars", VALID_CAR, format="json").data
        self.customer = make_customer()
        Customer.objects.filter(pk=self.customer.pk).update(email="client@example.kz")

    def finish_trip(self):
        booking = self.client.post(
            "/api/v1/bookings", {"car_id": self.car["id"], "customer_phone": "+77011234567"}, format="json"
        ).data
        response = self.client.patch(f"/api/v1/bookings/{booking['id']}", {"status": "finished"}, format="json")
        self.assertEqual(response.status_code, 200, response.content)
        return response.data

    def run_worker(self):
        with self.assertLogs("jobs", "INFO") as logs:
            Worker(worker_id="test-worker").run_pending()
        return "\n".join(logs.output)

    def payment(self, payment_id):
        return Payment.objects.get(pk=payment_id)

    def task(self, task_id):
        return BackgroundTask.objects.get(pk=task_id)


class EnqueueTests(PaymentQueueTestCase):
    """Завершение поездки ставит задачу и отвечает, не дожидаясь провайдера."""

    def test_finish_returns_pending_payment_without_calling_provider(self):
        with mock.patch.object(payment_gateway, "charge") as charge:
            trip = self.finish_trip()
        charge.assert_not_called()  # в запросе провайдер не вызывается вообще
        payment = trip["payment"]
        self.assertEqual(payment["status"], "pending")
        self.assertEqual(payment["task"]["status"], "READY")
        self.assertEqual(payment["task"]["attempts"], 0)
        task = self.task(payment["task_id"])
        self.assertEqual(task.task_path, "fleet.tasks.charge_payment")
        self.assertEqual(task.kwargs, {"payment_id": payment["id"]})

    def test_enqueue_is_part_of_callers_transaction(self):
        """Откат транзакции убирает и задачу: обработчик не увидит задачу без данных."""
        with self.assertRaises(RuntimeError), transaction.atomic():
            charge_payment.enqueue(payment_id="00000000-0000-4000-8000-000000000000")
            raise RuntimeError("rollback")
        self.assertEqual(BackgroundTask.objects.count(), 0)

    def test_enqueue_is_logged(self):
        with self.assertLogs("jobs", "INFO") as logs:
            self.finish_trip()
        self.assertIn("Задача charge_payment поставлена в очередь", "\n".join(logs.output))

    def test_result_readable_through_django_tasks_api(self):
        trip = self.finish_trip()
        result = charge_payment.get_result(trip["payment"]["task_id"])
        self.assertEqual(result.status, TaskResultStatus.READY)
        self.assertEqual(result.kwargs, {"payment_id": trip["payment"]["id"]})
        self.assertFalse(result.is_finished)
        with self.assertRaises(TaskResultDoesNotExist):
            default_task_backend.get_result("00000000-0000-4000-8000-000000000000")
        with self.assertRaises(TaskResultDoesNotExist):
            default_task_backend.get_result("not-a-uuid")

    def test_deferred_task_waits_for_run_after(self):
        later = charge_payment.using(run_after=timezone.now() + timedelta(hours=1))
        later.enqueue(payment_id="00000000-0000-4000-8000-000000000000")
        self.assertIsNone(Worker().claim())


class SuccessfulChargeTests(PaymentQueueTestCase):
    def test_worker_charges_payment_and_sends_receipt(self):
        trip = self.finish_trip()
        with provider("ok1") as charge:
            log = self.run_worker()
        charge.assert_called_once()
        self.assertEqual(charge.call_args.kwargs["payment_id"], trip["payment"]["id"])

        payment = self.payment(trip["payment"]["id"])
        self.assertEqual((payment.status, payment.provider_payment_id), ("paid", "ch_ok1"))
        self.assertIsNotNone(payment.paid_at)
        task = self.task(trip["payment"]["task_id"])
        self.assertEqual((task.status, task.attempts), ("SUCCESSFUL", 1))
        self.assertEqual(task.return_value["provider_payment_id"], "ch_ok1")
        self.assertIn("попытка 1 из 3", log)
        self.assertIn("выполнена за", log)

        # Цепочка: после списания поставлен и выполнен чек клиенту.
        receipt = BackgroundTask.objects.get(task_path="fleet.tasks.send_receipt")
        self.assertEqual(receipt.status, "SUCCESSFUL")
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ["client@example.kz"])
        self.assertIn(str(payment.amount), mail.outbox[0].body)

    def test_receipt_skipped_without_email(self):
        Customer.objects.filter(pk=self.customer.pk).update(email=None)
        self.finish_trip()
        with provider("ok1"):
            self.run_worker()
        self.assertEqual(BackgroundTask.objects.get(task_path="fleet.tasks.send_receipt").return_value, {"sent": False})
        self.assertEqual(mail.outbox, [])

    def test_already_paid_payment_is_not_charged_again(self):
        """Идемпотентность задачи: повторное выполнение не списывает деньги второй раз."""
        trip = self.finish_trip()
        with provider("ok1"):
            self.run_worker()
        with mock.patch.object(payment_gateway, "charge") as charge:
            result = services.process_payment(trip["payment"]["id"])
        charge.assert_not_called()
        self.assertEqual(result["status"], "paid")

    def test_run_worker_command_once(self):
        self.finish_trip()
        out = StringIO()
        with provider("ok1"), self.assertLogs("jobs", "INFO"):
            call_command("run_worker", "--once", stdout=out)
        self.assertIn("Выполнено задач: 2", out.getvalue())  # списание и чек


class RetryTests(PaymentQueueTestCase):
    """Ошибочный путь: повторы с логированием причины, окончательный сбой, повторная оплата."""

    def test_temporary_failure_is_retried_and_then_succeeds(self):
        trip = self.finish_trip()
        with provider(UNAVAILABLE, "ok2") as charge:
            log = self.run_worker()
        self.assertEqual(charge.call_count, 2)
        task = self.task(trip["payment"]["task_id"])
        self.assertEqual((task.status, task.attempts), ("SUCCESSFUL", 2))
        self.assertEqual([e["attempt"] for e in task.errors], [1])
        self.assertIn("PaymentProviderUnavailable", task.errors[0]["message"])
        self.assertEqual(self.payment(trip["payment"]["id"]).status, "paid")
        self.assertIn("WARNING:jobs:Задача", log)
        self.assertIn("сбой попытки 1 из 3", log)
        self.assertIn("Повтор через 0 с", log)

    def test_attempts_exhausted_marks_task_and_payment_failed(self):
        trip = self.finish_trip()
        with provider(UNAVAILABLE, UNAVAILABLE, UNAVAILABLE) as charge, \
                self.assertLogs("fleet.payments", "ERROR") as payment_logs:
            log = self.run_worker()
        self.assertEqual(charge.call_count, 3)
        task = self.task(trip["payment"]["task_id"])
        self.assertEqual((task.status, task.attempts), ("FAILED", 3))
        self.assertIsNotNone(task.finished_at)
        self.assertEqual(len(task.errors), 3)
        self.assertIn("Traceback", task.errors[-1]["traceback"])
        payment = self.payment(trip["payment"]["id"])
        self.assertEqual(payment.status, "failed")
        self.assertIn("unreachable", payment.failure_reason)
        self.assertIn("ERROR:jobs:Задача", log)
        self.assertIn("попытки исчерпаны", log)
        self.assertIn("Повторить оплату: POST /api/v1/bookings/", "\n".join(payment_logs.output))
        self.assertEqual(mail.outbox, [])

    def test_decline_is_not_retried(self):
        trip = self.finish_trip()
        with provider(PaymentDeclined("Provider declined with 402: Insufficient funds")) as charge, \
                self.assertLogs("fleet.payments", "ERROR"):
            log = self.run_worker()
        self.assertEqual(charge.call_count, 1)
        self.assertEqual(self.task(trip["payment"]["task_id"]).status, "FAILED")
        self.assertIn("ошибка не временная", log)
        self.assertIn("Insufficient funds", self.payment(trip["payment"]["id"]).failure_reason)

    def test_backoff_postpones_next_attempt(self):
        trip = self.finish_trip()
        tasks = {**TASKS_NO_BACKOFF["default"], "OPTIONS": {"MAX_ATTEMPTS": 3, "RETRY_BACKOFF_SECONDS": 60}}
        with override_settings(TASKS={"default": tasks}), provider(UNAVAILABLE) as charge:
            log = self.run_worker()
        self.assertEqual(charge.call_count, 1)  # второй попытки ещё не было — пауза
        task = self.task(trip["payment"]["task_id"])
        self.assertEqual(task.status, "READY")
        self.assertGreater(task.run_after, timezone.now() + timedelta(seconds=50))
        self.assertIn("Повтор через 60 с", log)
        self.assertEqual(self.payment(trip["payment"]["id"]).status, "pending")  # пока повторяем — не сбой

    def test_stale_running_task_is_taken_over(self):
        """Обработчик упал посреди задачи — по истечении аренды её забирает другой."""
        trip = self.finish_trip()
        BackgroundTask.objects.filter(pk=trip["payment"]["task_id"]).update(
            status="RUNNING", attempts=1, worker_ids=["dead-worker"],
            last_attempted_at=timezone.now() - timedelta(hours=1),
        )
        with provider("ok3"):
            log = self.run_worker()
        task = self.task(trip["payment"]["task_id"])
        self.assertEqual((task.status, task.attempts), ("SUCCESSFUL", 2))
        self.assertEqual(task.worker_ids, ["dead-worker", "test-worker"])
        self.assertIn("зависла", log)

    def test_stale_task_on_last_attempt_fails(self):
        trip = self.finish_trip()
        BackgroundTask.objects.filter(pk=trip["payment"]["task_id"]).update(
            status="RUNNING", attempts=3, last_attempted_at=timezone.now() - timedelta(hours=1),
        )
        with self.assertLogs("fleet.payments", "ERROR"):
            self.run_worker()
        self.assertEqual(self.task(trip["payment"]["task_id"]).status, "FAILED")
        self.assertEqual(self.payment(trip["payment"]["id"]).status, "failed")

    def test_unknown_task_path_fails_without_retries(self):
        row = BackgroundTask.objects.create(task_path="fleet.tasks.removed_task", max_attempts=3, run_after=timezone.now())
        log = self.run_worker()
        row.refresh_from_db()
        self.assertEqual((row.status, row.attempts), ("FAILED", 1))
        self.assertIn("Unknown task", row.last_error)
        self.assertIn("ошибка не временная", log)

    def test_failing_signal_receiver_does_not_crash_worker(self):
        self.finish_trip()
        with provider(UNAVAILABLE, UNAVAILABLE, UNAVAILABLE), \
                mock.patch.object(services, "fail_payment", side_effect=RuntimeError("db hiccup")):
            log = self.run_worker()
        self.assertIn("Обработчик сигнала mark_payment_failed", log)


class PaymentApiTests(PaymentQueueTestCase):
    """Статус платежа и повторная оплата через API."""

    def _fail(self, trip):
        with provider(UNAVAILABLE, UNAVAILABLE, UNAVAILABLE), self.assertLogs("fleet.payments", "ERROR"):
            self.run_worker()
        return self.client.get(f"/api/v1/payments/{trip['payment']['id']}").data

    def test_payment_status_shows_task_progress(self):
        trip = self.finish_trip()
        failed = self._fail(trip)
        self.assertEqual(failed["status"], "failed")
        self.assertEqual(failed["task"]["status"], "FAILED")
        self.assertEqual(failed["task"]["attempts"], 3)
        self.assertIn("unreachable", failed["task"]["last_error"])

    def test_retry_after_failure_returns_202_and_new_payment(self):
        trip = self.finish_trip()
        self._fail(trip)
        response = self.client.post(f"/api/v1/bookings/{trip['id']}/payments")
        self.assertEqual(response.status_code, 202, response.content)
        self.assertEqual(response["Location"], f"/api/v1/payments/{response.data['id']}")
        self.assertEqual(response.data["status"], "pending")
        self.assertEqual(response.data["task"]["status"], "READY")
        self.assertNotEqual(response.data["id"], trip["payment"]["id"])

        with provider("ok4"):
            self.run_worker()
        payments = self.client.get(f"/api/v1/bookings/{trip['id']}/payments").data["items"]
        self.assertEqual([p["status"] for p in payments], ["failed", "paid"])

    def test_retry_while_payment_in_progress_returns_409(self):
        trip = self.finish_trip()
        response = self.client.post(f"/api/v1/bookings/{trip['id']}/payments")
        self.assertError(response, 409, "payment_in_progress")

    def test_retry_of_paid_booking_returns_409(self):
        trip = self.finish_trip()
        with provider("ok5"):
            self.run_worker()
        self.assertError(self.client.post(f"/api/v1/bookings/{trip['id']}/payments"), 409, "payment_already_paid")

    def test_retry_of_cancelled_booking_returns_409(self):
        booking = self.client.post(
            "/api/v1/bookings", {"car_id": self.car["id"], "customer_phone": "+77011234567"}, format="json"
        ).data
        self.client.patch(f"/api/v1/bookings/{booking['id']}", {"status": "cancelled"}, format="json")
        self.assertError(self.client.post(f"/api/v1/bookings/{booking['id']}/payments"), 409, "booking_not_payable")

    def test_unknown_payment_returns_404(self):
        self.assertError(self.client.get("/api/v1/payments/00000000-0000-4000-8000-000000000000"), 404, "payment_not_found")
        self.assertError(self.client.get("/api/v1/payments/not-a-uuid"), 404, "payment_not_found")

    def test_customer_sees_and_retries_only_own_payment(self):
        trip = self.finish_trip()
        self._fail(trip)
        owner = make_account(Role.CUSTOMER, email="owner@test.kz", customer_id=self.customer.id)
        stranger = make_account(Role.CUSTOMER, email="stranger@test.kz",
                                customer_id=make_customer(phone="+77017777777").id)

        authenticate(self.client, stranger)
        self.assertError(self.client.get(f"/api/v1/payments/{trip['payment']['id']}"), 403, "permission_denied")
        self.assertError(self.client.post(f"/api/v1/bookings/{trip['id']}/payments"), 403, "permission_denied")

        authenticate(self.client, owner)
        self.assertEqual(self.client.get(f"/api/v1/payments/{trip['payment']['id']}").status_code, 200)
        self.assertEqual(self.client.post(f"/api/v1/bookings/{trip['id']}/payments").status_code, 202)

    def test_database_allows_one_pending_payment_per_booking(self):
        trip = self.finish_trip()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Payment.objects.create(booking_id=trip["id"], amount=1, status="pending")

    def test_failed_payment_needs_reason(self):
        trip = self.finish_trip()
        with self.assertRaises(IntegrityError), transaction.atomic():
            Payment.objects.filter(pk=trip["payment"]["id"]).update(status="failed")


class TaskApiTests(PaymentQueueTestCase):
    """GET /api/v1/tasks — очередь для персонала, без стектрейсов."""

    def test_staff_lists_and_filters_tasks(self):
        trip = self.finish_trip()
        with provider(UNAVAILABLE, "ok6"):
            self.run_worker()
        body = self.client.get("/api/v1/tasks?task=charge_payment").data
        self.assertEqual(body["total"], 1)
        item = body["items"][0]
        self.assertEqual((item["status"], item["attempts"]), ("SUCCESSFUL", 2))
        self.assertEqual(item["errors"][0]["attempt"], 1)
        self.assertNotIn("traceback", item["errors"][0])
        self.assertEqual(self.client.get("/api/v1/tasks?status=FAILED").data["total"], 0)
        self.assertEqual(self.client.get(f"/api/v1/tasks/{trip['payment']['task_id']}").data["id"], item["id"])

    def test_ready_task_shows_next_attempt(self):
        trip = self.finish_trip()
        task = self.client.get(f"/api/v1/tasks/{trip['payment']['task_id']}").data
        self.assertEqual(task["status"], "READY")
        self.assertIsNotNone(task["next_attempt_at"])

    def test_invalid_filter_returns_400(self):
        self.assertError(self.client.get("/api/v1/tasks?status=DONE"), 400, "validation_error")

    def test_unknown_task_returns_404(self):
        self.assertError(self.client.get("/api/v1/tasks/00000000-0000-4000-8000-000000000000"), 404, "task_not_found")
        self.assertError(self.client.get("/api/v1/tasks/xyz"), 404, "task_not_found")


class TaskApiAccessTests(ApiTestCase):
    def test_customer_cannot_see_queue(self):
        authenticate(self.client, make_account(Role.CUSTOMER))
        self.assertError(self.client.get("/api/v1/tasks"), 403, "permission_denied")

    def test_operator_can_see_queue(self):
        authenticate(self.client, make_account(Role.OPERATOR))
        self.assertEqual(self.client.get("/api/v1/tasks").status_code, 200)


class WorkerLoopTests(PaymentQueueTestCase):
    """Цикл run(): ждёт при пустой очереди, переживает недоступность базы, останавливается по Ctrl+C."""

    def setUp(self):
        super().setUp()
        # run() закрывает устаревшие соединения с базой; в тесте соединение держит транзакцию теста.
        keep = mock.patch("jobs.worker.close_old_connections")
        keep.start()
        self.addCleanup(keep.stop)

    def test_loop_processes_queue_then_waits(self):
        self.finish_trip()
        worker = Worker(worker_id="loop")
        sleeps = []

        def fake_sleep(seconds):
            sleeps.append(seconds)
            worker.stop()

        with provider("ok7"), mock.patch("jobs.worker.time.sleep", side_effect=fake_sleep), \
                self.assertLogs("jobs", "INFO") as logs:
            worker.run(interval=0.5)
        self.assertEqual(sleeps, [0.5])
        self.assertEqual(Payment.objects.get().status, "paid")
        self.assertIn("Обработчик loop запущен", logs.output[0])
        self.assertIn("Обработчик loop остановлен", logs.output[-1])

    def test_database_outage_is_logged_and_survived(self):
        from django.db import OperationalError

        worker = Worker(worker_id="loop")
        outcomes = [OperationalError("connection refused"), 0]

        def fake_run_pending():
            outcome = outcomes.pop(0)
            if isinstance(outcome, Exception):
                raise outcome
            worker.stop()
            return outcome

        with mock.patch.object(worker, "run_pending", side_effect=fake_run_pending), \
                mock.patch("jobs.worker.time.sleep") as sleep, self.assertLogs("jobs", "INFO") as logs:
            worker.run(interval=1)
        sleep.assert_any_call(5)
        self.assertIn("База данных недоступна: OperationalError: connection refused", "\n".join(logs.output))

    def test_ctrl_c_stops_worker_gracefully(self):
        worker = Worker(worker_id="loop")
        with mock.patch.object(worker, "run_pending", side_effect=KeyboardInterrupt), \
                self.assertLogs("jobs", "INFO") as logs:
            worker.run()
        self.assertIn("остановлен", logs.output[-1])

    def test_run_worker_command_starts_loop(self):
        with mock.patch.object(Worker, "run") as run:
            call_command("run_worker", "--interval", "2", stdout=StringIO())
        run.assert_called_once_with(interval=2.0)

    def test_admin_requeues_failed_task(self):
        trip = self.finish_trip()
        with provider(PaymentDeclined("declined")), self.assertLogs("fleet.payments", "ERROR"):
            self.run_worker()
        from django.contrib.admin.sites import site

        from jobs.admin import BackgroundTaskAdmin

        admin = BackgroundTaskAdmin(BackgroundTask, site)
        with mock.patch.object(admin, "message_user") as message:
            admin.requeue(request=None, queryset=BackgroundTask.objects.all())
        task = self.task(trip["payment"]["task_id"])
        self.assertEqual((task.status, task.attempts), ("READY", 0))
        message.assert_called_once()
