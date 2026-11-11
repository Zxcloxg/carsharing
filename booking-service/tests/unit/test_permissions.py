"""Модульные тесты правил доступа (accounts/permissions.py): декораторы маршрутов и проверка владельца.

Запрос и пользователь подменяются простыми объектами — ни HTTP, ни базы не нужно.
"""
from types import SimpleNamespace

from django.test import SimpleTestCase
from rest_framework.exceptions import NotAuthenticated

from accounts.authentication import CurrentUser
from accounts.errors import AccessDenied
from accounts.permissions import ensure_fields_allowed, ensure_owner_or_staff, is_staff, require_auth, require_role

ALICE = CurrentUser("u1", "alice@test.kz", "customer", "cust-1")
CUSTOMER_WITHOUT_PROFILE = CurrentUser("u2", "new@test.kz", "customer", None)
OPERATOR = CurrentUser("u3", "op@test.kz", "operator", None)
ADMIN = CurrentUser("u4", "admin@test.kz", "admin", None)


class View:
    @require_auth
    def profile(self, request):
        return "ok"

    @require_role("admin")
    def delete_car(self, request):
        return "deleted"

    @require_role("operator", "admin")
    def review(self, request):
        return "reviewed"


def call(method, user):
    return getattr(View(), method)(SimpleNamespace(user=user))


class RouteDecoratorTests(SimpleTestCase):
    def test_anonymous_gets_401_everywhere(self):
        for method in ("profile", "delete_car", "review"):
            with self.subTest(method=method), self.assertRaises(NotAuthenticated):
                call(method, None)

    def test_authentication_checked_before_role(self):
        """Аноним на маршруте администратора получает 401, а не 403: сначала «кто ты», потом «что тебе можно»."""
        with self.assertRaises(NotAuthenticated):
            call("delete_car", None)

    def test_wrong_role_gets_403_with_allowed_roles(self):
        with self.assertRaises(AccessDenied) as ctx:
            call("delete_car", OPERATOR)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertIn("admin", ctx.exception.details[0]["message"])
        self.assertIn("your role: operator", ctx.exception.details[0]["message"])

    def test_allowed_roles_pass(self):
        self.assertEqual(call("profile", ALICE), "ok")
        self.assertEqual(call("delete_car", ADMIN), "deleted")
        self.assertEqual(call("review", OPERATOR), "reviewed")
        self.assertEqual(call("review", ADMIN), "reviewed")

    def test_customer_cannot_use_staff_route(self):
        with self.assertRaises(AccessDenied):
            call("review", ALICE)

    def test_decorator_keeps_view_name(self):
        self.assertEqual(View.delete_car.__name__, "delete_car")


class OwnershipTests(SimpleTestCase):
    def test_owner_allowed(self):
        self.assertIsNone(ensure_owner_or_staff(ALICE, "cust-1"))

    def test_uuid_and_string_ids_compared_as_text(self):
        import uuid

        customer_id = uuid.UUID("8d1f6c1e-0e4c-4b0a-9d4f-1a2b3c4d5e6f")
        owner = CurrentUser("u9", "x@test.kz", "customer", str(customer_id))
        self.assertIsNone(ensure_owner_or_staff(owner, customer_id))

    def test_other_customer_denied(self):
        with self.assertRaises(AccessDenied) as ctx:
            ensure_owner_or_staff(ALICE, "cust-2")
        self.assertEqual(ctx.exception.message, "You can access only your own data")

    def test_customer_without_profile_denied(self):
        """Пустой customer_id не должен совпасть с «пустым» владельцем записи."""
        for record_owner in (None, "", "None"):
            with self.subTest(record_owner=record_owner), self.assertRaises(AccessDenied):
                ensure_owner_or_staff(CUSTOMER_WITHOUT_PROFILE, record_owner)

    def test_staff_sees_any_record(self):
        self.assertTrue(is_staff(OPERATOR))
        self.assertTrue(is_staff(ADMIN))
        self.assertFalse(is_staff(ALICE))
        self.assertIsNone(ensure_owner_or_staff(OPERATOR, "cust-2"))


class FieldLevelTests(SimpleTestCase):
    """Права на уровне полей: операция разрешена роли, но не каждое поле."""

    ALLOWED = {"status", "fuel_level"}

    def test_allowed_fields_pass(self):
        self.assertIsNone(ensure_fields_allowed(OPERATOR, {"status": "service"}, self.ALLOWED))
        self.assertIsNone(ensure_fields_allowed(OPERATOR, {"status": "free", "fuel_level": 0.5}, self.ALLOWED))

    def test_forbidden_field_listed(self):
        with self.assertRaises(AccessDenied) as ctx:
            ensure_fields_allowed(OPERATOR, {"status": "free", "price_per_minute": 1, "plate": "X"}, self.ALLOWED)
        self.assertEqual([d["field"] for d in ctx.exception.details], ["plate", "price_per_minute"])
        self.assertIn("fuel_level, status", ctx.exception.details[0]["message"])

    def test_operator_car_fields_match_role_matrix(self):
        from fleet.views import OPERATOR_CAR_FIELDS

        self.assertEqual(OPERATOR_CAR_FIELDS, {"status", "fuel_level"})
