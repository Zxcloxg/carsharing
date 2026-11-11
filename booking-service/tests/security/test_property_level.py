"""Права на уровне полей и массовое присваивание (OWASP API3:2023 — BOPLA, API5:2023 — BFLA).

По матрице ролей (docs/auth.md) оператор меняет у машины только статус и
уровень топлива — то, что он видит при обслуживании. Тариф, госномер, марку,
модель, год и цвет меняет только администратор. Поля, которые задаёт сервер
(роль, тариф брони, статус клиента, хэш пароля), из тела запроса не принимаются.
"""
from decimal import Decimal

from accounts.models import Role, User
from fleet import services as fleet_services
from fleet.models import Car
from tests.helpers import (
    REGISTER,
    TEST_PASSWORD,
    VALID_CAR,
    ApiTestCase,
    authenticate,
    make_account,
    make_customer,
)


class CarFieldsByRoleTests(ApiTestCase):
    def setUp(self):
        super().setUp()
        self.car = fleet_services.create_car({**VALID_CAR, "status": "free"})
        self.url = f"/api/v1/cars/{self.car['id']}"
        self.operator = make_account(Role.OPERATOR)
        self.admin = make_account(Role.ADMIN)

    def _patch_as(self, user, changes):
        authenticate(self.client, user)
        return self.client.patch(self.url, changes, format="json")

    def test_operator_changes_status_and_fuel(self):
        response = self._patch_as(self.operator, {"status": "service", "fuel_level": 0.35})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual((response.data["status"], response.data["fuel_level"]), ("service", 0.35))

    def test_operator_cannot_change_price(self):
        """До исправления оператор мог поставить тариф 1 ₸/мин и бронировать машину почти бесплатно."""
        error = self.assertError(self._patch_as(self.operator, {"price_per_minute": 1}), 403, "permission_denied")
        self.assertEqual(error["details"][0]["field"], "price_per_minute")
        self.assertEqual(Car.objects.get(pk=self.car["id"]).price_per_minute, Decimal("80.00"))

    def test_operator_cannot_change_identity_fields(self):
        for changes in ({"plate": "999ZZZ02"}, {"brand": "Lada"}, {"model": "Granta"}, {"year": 2027},
                        {"color": "pink"}):
            with self.subTest(changes=changes):
                self.assertError(self._patch_as(self.operator, changes), 403, "permission_denied")

    def test_mixed_request_changes_nothing(self):
        """Разрешённое поле вместе с запрещённым: запрос отклоняется целиком, статус тоже не меняется."""
        self.assertError(
            self._patch_as(self.operator, {"status": "service", "price_per_minute": 1}), 403, "permission_denied"
        )
        car = Car.objects.get(pk=self.car["id"])
        self.assertEqual((car.status, car.price_per_minute), ("free", Decimal("80.00")))

    def test_admin_changes_any_field(self):
        response = self._patch_as(self.admin, {"price_per_minute": 95, "plate": "999ZZZ02"})
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(response.data["plate"], "999ZZZ02")


class ServerControlledFieldsTests(ApiTestCase):
    def test_role_cannot_be_set_at_registration(self):
        response = self.client.post(
            REGISTER, {"email": "evil@test.kz", "password": TEST_PASSWORD, "role": "admin"}, format="json"
        )
        self.assertError(response, 400, "validation_error")
        self.assertFalse(User.objects.filter(email="evil@test.kz").exists())

    def test_booking_price_taken_from_car_not_from_body(self):
        car = fleet_services.create_car({**VALID_CAR, "status": "free"})
        customer = make_customer()
        authenticate(self.client, make_account(customer_id=customer.id))
        response = self.client.post(
            "/api/v1/bookings",
            {"car_id": car["id"], "price_per_minute": 1, "status": "finished", "total_cost": 0},
            format="json",
        )
        self.assertEqual(response.status_code, 201, response.content)
        self.assertEqual(Decimal(response.data["price_per_minute"]), Decimal("80.00"))
        self.assertEqual(response.data["status"], "active")

    def test_customer_cannot_unblock_self(self):
        customer = make_customer()
        customer.status = "blocked"
        customer.save()
        authenticate(self.client, make_account(customer_id=customer.id))
        response = self.client.patch(f"/api/v1/customers/{customer.id}", {"status": "active"}, format="json")
        self.assertError(response, 403, "permission_denied")

    def test_admin_cannot_overwrite_password_hash_or_email(self):
        admin = make_account(Role.ADMIN)
        target = make_account(email="target@test.kz")
        before = User.objects.get(pk=target["id"]).password_hash
        authenticate(self.client, admin)
        response = self.client.patch(
            f"/api/v1/users/{target['id']}", {"password_hash": "argon2$x$y", "email": "mine@test.kz"}, format="json"
        )
        self.assertError(response, 400, "validation_error")
        user = User.objects.get(pk=target["id"])
        self.assertEqual((user.email, user.password_hash), ("target@test.kz", before))
