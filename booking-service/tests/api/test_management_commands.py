"""Интеграционные тесты команд управления: демо-данные, создание администратора, демонстрация отката."""
from io import StringIO
from unittest import mock

from django.core.management import CommandError, call_command
from django.test import SimpleTestCase

from accounts.models import User
from config.db import log_database_connection
from fleet.management.commands.seed_demo import CARS, CUSTOMERS, USERS
from fleet.models import Booking, Car, CarStatusChange, Customer, Incident, Payment
from tests.helpers import LOGIN, ApiTestCase


def run(command, *args, **options):
    out, err = StringIO(), StringIO()
    call_command(command, *args, stdout=out, stderr=err, **options)
    return out.getvalue(), err.getvalue()


class SeedDemoTests(ApiTestCase):
    """Команда seed_demo: заполняет все части домена, повторный запуск ничего не дублирует."""

    def test_fills_every_part_of_domain(self):
        run("seed_demo")
        self.assertEqual(Car.objects.count(), len(CARS))
        self.assertEqual(Customer.objects.count(), len(CUSTOMERS))
        self.assertEqual(User.objects.count(), len(USERS))
        self.assertEqual(Booking.objects.filter(status="active").count(), 1)
        self.assertEqual(Payment.objects.filter(status="paid").count(), 1)
        self.assertEqual(Incident.objects.count(), 1)
        self.assertEqual(Customer.objects.get(phone="+77779998877").status, "blocked")

    def test_second_run_adds_nothing(self):
        run("seed_demo")
        out, _ = run("seed_demo")
        self.assertIn("демо-данные автопарка не добавлены", out)
        self.assertEqual(Car.objects.count(), len(CARS))
        self.assertEqual(User.objects.count(), len(USERS))

    def test_demo_accounts_can_log_in(self):
        run("seed_demo")
        for email, password, role, phone in USERS:
            with self.subTest(email=email):
                response = self.client.post(LOGIN, {"email": email, "password": password}, format="json")
                self.assertEqual(response.status_code, 200, response.content)
                self.assertEqual(response.data["user"]["role"], role)
                self.assertEqual(response.data["user"]["customer_id"] is not None, phone is not None)


class CreateUserTests(ApiTestCase):
    """Команда create_user: роль, правила пароля, занятый email, ввод пароля с подтверждением."""

    def test_creates_admin(self):
        out, _ = run("create_user", "Boss@Carsharing.kz", role="admin", password="Steppe#Eagle2026")
        self.assertIn("Created admin boss@carsharing.kz", out)
        self.assertEqual(User.objects.get(email="boss@carsharing.kz").role, "admin")

    def test_password_rules_apply(self):
        with self.assertRaisesMessage(CommandError, "password"):
            run("create_user", "boss@carsharing.kz", role="admin", password="12345678")
        self.assertFalse(User.objects.exists())

    def test_duplicate_email(self):
        run("create_user", "boss@carsharing.kz", password="Steppe#Eagle2026")
        with self.assertRaisesMessage(CommandError, "already exists"):
            run("create_user", "BOSS@carsharing.kz", password="Steppe#Eagle2026")

    def test_interactive_password_must_match(self):
        with mock.patch("accounts.management.commands.create_user.getpass", side_effect=["Steppe#Eagle2026", "typo"]):
            with self.assertRaisesMessage(CommandError, "Passwords do not match"):
                run("create_user", "boss@carsharing.kz")

    def test_interactive_password(self):
        with mock.patch("accounts.management.commands.create_user.getpass", return_value="Steppe#Eagle2026"):
            run("create_user", "op@carsharing.kz", role="operator")
        self.assertEqual(User.objects.get(email="op@carsharing.kz").role, "operator")


class DemoTransactionTests(ApiTestCase):
    """Команда demo_transaction: сбойные транзакции не оставляют следов в базе."""

    def test_needs_demo_data(self):
        _, err = run("demo_transaction")
        self.assertIn("seed_demo", err)

    def test_failed_steps_leave_no_trace(self):
        """Сбойные транзакции ничего не оставляют: ни платежа, ни занятой машины, ни активной брони.

        Сценарий 2 сам создаёт бронь для демонстрации и в конце отменяет её — в истории остаётся
        одна отменённая бронь и две записи журнала статусов (free -> booked -> free).
        """
        run("seed_demo")
        payments = Payment.objects.count()
        statuses = list(Car.objects.order_by("plate").values_list("status", flat=True))
        bookings, history = Booking.objects.count(), CarStatusChange.objects.count()

        out, _ = run("demo_transaction")

        self.assertEqual(Payment.objects.count(), payments)
        self.assertEqual(list(Car.objects.order_by("plate").values_list("status", flat=True)), statuses)
        self.assertEqual(Booking.objects.count(), bookings + 1)
        self.assertEqual(Booking.objects.latest("started_at").status, "cancelled")
        self.assertEqual(CarStatusChange.objects.count(), history + 2)
        self.assertEqual(out.count("ROLLBACK"), 2)


class DatabaseConnectionLogTests(SimpleTestCase):
    """Проверка подключения при старте сервиса — на подменённом соединении, без настоящей базы."""

    def _connection(self, cursor):
        connection = mock.MagicMock()
        connection.settings_dict = {"USER": "carsharing", "HOST": "db", "PORT": "5432", "NAME": "carsharing"}
        connection.cursor.return_value.__enter__.return_value = cursor
        return connection

    def test_logs_server_version_and_target(self):
        cursor = mock.Mock(fetchone=mock.Mock(return_value=("17.6",)))
        connection = self._connection(cursor)
        with mock.patch("config.db.connection", connection), self.assertLogs("config.db", "INFO") as logs:
            log_database_connection()
        self.assertIn("PostgreSQL 17.6, carsharing@db:5432/carsharing", logs.output[0])
        self.assertNotIn("password", logs.output[0].lower())
        connection.close.assert_called_once()

    def test_failure_is_logged_and_stops_start(self):
        cursor = mock.Mock(execute=mock.Mock(side_effect=OSError("connection refused")))
        with mock.patch("config.db.connection", self._connection(cursor)), \
                self.assertLogs("config.db", "ERROR") as logs, self.assertRaises(OSError):
            log_database_connection()
        self.assertIn("carsharing@db:5432/carsharing", logs.output[0])
