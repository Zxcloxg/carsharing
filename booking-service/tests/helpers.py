"""Общие данные и помощники автотестов.

Изоляция (ПР5, п. 3):
* Django создаёт для прогона отдельную базу test_<DB_NAME> и накатывает на неё
  все миграции; рабочая база не затрагивается;
* каждый тест ApiTestCase выполняется внутри транзакции, которая
  откатывается после теста, — данные, созданные тестом, исчезают;
* кэш (счётчики ограничения частоты входа) очищается перед каждым тестом,
  поэтому тесты не зависят от того, кто выполнялся раньше.
Проверка: python manage.py test tests --shuffle (случайный порядок) и --reverse.
"""
from datetime import date

from django.core.cache import cache
from django.utils import timezone
from rest_framework.test import APITestCase

from accounts.models import Role
from accounts.repository import UserRepository
from accounts.tokens import issue_access_token
from fleet.models import Customer, DriverLicense

TEST_PASSWORD = "Str0ng#Passw0rd"

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"

VALID_CUSTOMER = {
    "phone": "+77011234567",
    "full_name": "Айгерим Сапарова",
    "birth_date": "1995-04-12",
    "email": "aigerim@example.kz",
}

VALID_LICENSE = {
    "number": "KZ0123456",
    "categories": "B",
    "issued_on": "2018-06-01",
    "expires_on": "2028-06-01",
}

VALID_CAR = {
    "plate": "001AAA02",
    "brand": "Toyota",
    "model": "Corolla",
    "year": 2024,
    "color": "black",
    "price_per_minute": 80,
    "fuel_level": 0.9,
}

users = UserRepository()


def make_account(role=Role.CUSTOMER, email=None, customer_id=None, password=TEST_PASSWORD):
    """Учётная запись нужной роли — напрямую через репозиторий, без запросов к API."""
    email = email or f"{role}-{users.search()[1] + 1}@test.kz"
    user = users.add(email=email, raw_password=password, role=role)
    if customer_id:
        user = users.update(user["id"], {"customer_id": str(customer_id)})
    return user


def authenticate(client, user):
    """Подставляет в тестовый клиент заголовок Authorization: Bearer <token>."""
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_access_token(user)}")
    return user


def make_customer(phone="+77011234567", approved=True):
    """Клиент с подтверждённым (или ожидающим проверки) удостоверением — в обход API."""
    customer = Customer.objects.create(phone=phone, full_name="Тестовый Клиент", birth_date=date(1990, 1, 1))
    DriverLicense.objects.create(
        customer=customer, number=f"KZ{phone[-7:]}", categories="B",
        issued_on=date(2015, 1, 1), expires_on=date(2035, 1, 1),
        status="approved" if approved else "pending", reviewed_at=timezone.now() if approved else None,
    )
    return customer


class ApiTestCase(APITestCase):
    """База API-тестов: транзакция с откатом (APITestCase) плюс чистый кэш перед каждым тестом."""

    def setUp(self):
        super().setUp()
        cache.clear()

    def assertError(self, response, status_code, code):
        """Ответ — ошибка в едином формате {"error": {"code", "message", "details"}}."""
        self.assertEqual(response.status_code, status_code, response.content)
        body = response.json()
        self.assertEqual(set(body), {"error"})
        self.assertEqual(set(body["error"]), {"code", "message", "details"})
        self.assertEqual(body["error"]["code"], code)
        self.assertIsInstance(body["error"]["message"], str)
        self.assertIsInstance(body["error"]["details"], list)
        return body["error"]


class StaffApiTestCase(ApiTestCase):
    """Запросы от имени администратора — роли с полным набором прав."""

    def setUp(self):
        super().setUp()
        authenticate(self.client, make_account(Role.ADMIN))
