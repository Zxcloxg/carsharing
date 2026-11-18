"""Лишние данные в ответах (OWASP API3:2023 — Excessive Data Exposure).

Все ответы, в которых есть учётная запись, обходятся рекурсивно: ни на какой
глубине не должно быть пароля, хэша, соли или секрета. Для учётной записи
дополнительно зафиксирован точный набор полей — новое поле в ответе придётся
добавить в тест осознанно.
"""
import re

from accounts.models import Role
from tests.helpers import LOGIN, ME, REGISTER, TEST_PASSWORD, VALID_CUSTOMER, ApiTestCase, authenticate, make_account

SENSITIVE = re.compile(r"pass|hash|secret|salt", re.IGNORECASE)
USER_FIELDS = {"id", "email", "role", "customer_id", "is_active", "created_at", "last_login_at"}


def sensitive_keys(data, prefix=""):
    found = []
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else key
            if SENSITIVE.search(key):
                found.append(path)
            found += sensitive_keys(value, path)
    elif isinstance(data, list):
        for item in data:
            found += sensitive_keys(item, f"{prefix}[]")
    return found


class DataExposureTests(ApiTestCase):
    """В ответах нет пароля, хэша и секретов ни на какой глубине."""

    def setUp(self):
        super().setUp()
        self.admin = make_account(Role.ADMIN, email="admin@test.kz")
        self.customer = make_account(email="client@test.kz")

    def assertClean(self, response):
        self.assertLess(response.status_code, 300, response.content)
        self.assertEqual(sensitive_keys(response.json()), [])
        self.assertNotIn(b"argon2", response.content)
        self.assertNotIn(TEST_PASSWORD.encode(), response.content)

    def test_register_and_login(self):
        registered = self.client.post(REGISTER, {"email": "new@test.kz", "password": TEST_PASSWORD}, format="json")
        self.assertClean(registered)
        self.assertEqual(set(registered.json()), USER_FIELDS)

        logged_in = self.client.post(LOGIN, {"email": "new@test.kz", "password": TEST_PASSWORD}, format="json")
        self.assertClean(logged_in)
        self.assertEqual(set(logged_in.json()), {"access_token", "token_type", "expires_in", "user"})
        self.assertEqual(set(logged_in.json()["user"]), USER_FIELDS)

    def test_token_payload_has_no_personal_data(self):
        import jwt

        token = self.client.post(LOGIN, {"email": "client@test.kz", "password": TEST_PASSWORD}, format="json").data
        claims = jwt.decode(token["access_token"], options={"verify_signature": False})
        self.assertEqual(set(claims), {"sub", "role", "iss", "iat", "exp"})  # без email и хэша: токен читает любой

    def test_me_and_user_management(self):
        authenticate(self.client, self.customer)
        self.assertClean(self.client.get(ME))

        authenticate(self.client, self.admin)
        listing = self.client.get("/api/v1/users", {"limit": 100})
        self.assertClean(listing)
        self.assertTrue(all(set(user) == USER_FIELDS for user in listing.json()["items"]))
        self.assertClean(self.client.get(f"/api/v1/users/{self.customer['id']}"))
        self.assertClean(self.client.patch(f"/api/v1/users/{self.customer['id']}", {"role": "operator"}, format="json"))

    def test_customer_profile(self):
        authenticate(self.client, self.customer)
        created = self.client.post("/api/v1/customers", VALID_CUSTOMER, format="json")
        self.assertClean(created)
        self.assertClean(self.client.get(f"/api/v1/customers/{created.data['id']}"))

    def test_error_responses(self):
        for response in (
            self.client.post(LOGIN, {"email": "client@test.kz", "password": "Wrong#Pass0"}, format="json"),
            self.client.post(REGISTER, {"email": "client@test.kz", "password": TEST_PASSWORD}, format="json"),
        ):
            with self.subTest(code=response.status_code):
                self.assertEqual(sensitive_keys(response.json()), [])
                self.assertNotIn(b"argon2", response.content)
