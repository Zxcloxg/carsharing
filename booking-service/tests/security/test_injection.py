"""Инъекции (OWASP API8:2023 / Injection).

Две проверки: статическая — в коде нет SQL, собранного из строк; динамическая —
SQL-фрагменты в параметрах, пути и теле запроса не исполняются, не дают 500 и
сохраняются в базе как обычный текст.
"""
import ast
from pathlib import Path

from django.conf import settings
from django.test import SimpleTestCase
from rest_framework import status

from accounts.models import Role
from fleet.models import Car
from tests.helpers import LOGIN, VALID_CAR, ApiTestCase, authenticate, make_account

PAYLOADS = [
    "' OR '1'='1",
    "Toyota'; DROP TABLE cars; --",
    "%' UNION SELECT password_hash FROM users --",
    "\" OR \"\"=\"",
    "1; SELECT pg_sleep(5)",
]

RAW_SQL_CALLS = {"raw", "extra", "execute", "executemany", "RawSQL"}
SKIP_DIRS = {".venv", "migrations", "tests", "__pycache__"}


def project_sources():
    root = Path(settings.BASE_DIR)
    for path in root.rglob("*.py"):
        if not SKIP_DIRS & set(path.relative_to(root).parts):
            yield root, path


class NoStringBuiltSqlTests(SimpleTestCase):
    """В исходниках нет SQL, собранного из строк."""

    def test_raw_sql_only_with_constant_query(self):
        """Вызовы raw()/extra()/execute()/RawSQL допустимы только с литералом без подстановок."""
        offenders, checked = [], 0
        for root, path in project_sources():
            checked += 1
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.Call):
                    continue
                name = node.func.attr if isinstance(node.func, ast.Attribute) else getattr(node.func, "id", None)
                if name not in RAW_SQL_CALLS:
                    continue
                query = node.args[0] if node.args else None
                if not (isinstance(query, ast.Constant) and isinstance(query.value, str)):
                    offenders.append(f"{path.relative_to(root)}:{node.lineno} {name}()")
        self.assertGreater(checked, 30)
        self.assertEqual(offenders, [])


class InjectionTests(ApiTestCase):
    """SQL-фрагменты в параметрах, пути и теле не исполняются и не дают 500."""

    def setUp(self):
        super().setUp()
        self.admin = make_account(Role.ADMIN, email="admin@test.kz")
        authenticate(self.client, self.admin)
        self.client.post("/api/v1/cars", VALID_CAR, format="json")
        self.client.credentials()

    def test_payload_in_filter_is_plain_text(self):
        for payload in PAYLOADS:
            with self.subTest(payload=payload):
                response = self.client.get("/api/v1/cars", {"brand": payload})
                self.assertEqual(response.status_code, status.HTTP_200_OK)
                self.assertEqual(response.data["total"], 0)
        self.assertEqual(Car.objects.count(), 1)

    def test_like_wildcards_are_escaped(self):
        """«%» и «_» в фильтре ищутся буквально, а не как шаблон LIKE «любая строка»."""
        for wildcard in ("%", "_", "%%"):
            with self.subTest(wildcard=wildcard):
                self.assertEqual(self.client.get("/api/v1/cars", {"brand": wildcard}).data["total"], 0)

    def test_payload_in_typed_filters_is_400(self):
        for params in ({"max_price_per_minute": "1 OR 1=1"}, {"status": "free' OR '1'='1"}, {"limit": "10; --"}):
            with self.subTest(params=params):
                self.assertError(self.client.get("/api/v1/cars", params), 400, "validation_error")

    def test_payload_in_path_id_is_404(self):
        for payload in ("' OR 1=1 --", "1;DROP TABLE cars", "1 UNION SELECT 1"):
            with self.subTest(payload=payload):
                self.assertError(self.client.get(f"/api/v1/cars/{payload}"), 404, "car_not_found")

    def test_payload_in_login(self):
        self.assertError(
            self.client.post(LOGIN, {"email": "admin@test.kz' --", "password": "x"}, format="json"), 400, "validation_error"
        )
        self.assertError(
            self.client.post(LOGIN, {"email": "admin@test.kz", "password": "' OR '1'='1"}, format="json"),
            401, "invalid_credentials",
        )

    def test_payload_in_body_is_stored_verbatim(self):
        authenticate(self.client, self.admin)
        brand = "Robert'); DROP TABLE cars;--"
        response = self.client.post("/api/v1/cars", {**VALID_CAR, "plate": "002AAA02", "brand": brand}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(self.client.get(f"/api/v1/cars/{response.data['id']}").data["brand"], brand)
        self.assertEqual(Car.objects.count(), 2)
