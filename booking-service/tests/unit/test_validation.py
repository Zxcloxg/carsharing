"""Модульные тесты схем валидации входных данных (fleet/serializers.py, accounts/serializers.py).

Схема — первая линия защиты: некорректное поле должно отсекаться до
бизнес-логики и базы. Тесты вызывают схемы напрямую, без HTTP и без базы.
"""
from datetime import date, timedelta

from django.test import SimpleTestCase

from accounts.serializers import LoginSerializer, RegisterSerializer, UserListQuerySerializer, UserPatchSerializer
from fleet.serializers import (
    BookingCreateSerializer,
    CarListQuerySerializer,
    CarPatchSerializer,
    CarSerializer,
    CustomerSerializer,
    DriverLicenseSerializer,
    LicenseReviewSerializer,
)
from tests.helpers import VALID_CAR, VALID_CUSTOMER, VALID_LICENSE


def errors_of(serializer):
    serializer.is_valid()
    return set(serializer.errors)


class CarValidationTests(SimpleTestCase):
    """Схема машины: госномер, цена, топливо, статус, лимит страницы."""

    def test_valid_car_gets_defaults(self):
        body = CarSerializer(data={k: v for k, v in VALID_CAR.items() if k not in ("color", "fuel_level")})
        self.assertTrue(body.is_valid(), body.errors)
        self.assertEqual(body.validated_data["status"], "free")
        self.assertEqual(body.validated_data["fuel_level"], 1.0)

    def test_plate_format(self):
        for plate in ("12", "001 AAA 02", "001aaa02", "<script>"):
            with self.subTest(plate=plate):
                self.assertIn("plate", errors_of(CarSerializer(data={**VALID_CAR, "plate": plate})))

    def test_price_must_be_positive(self):
        for price in (0, -5, "free"):
            with self.subTest(price=price):
                self.assertIn("price_per_minute", errors_of(CarSerializer(data={**VALID_CAR, "price_per_minute": price})))

    def test_fuel_level_is_share(self):
        self.assertIn("fuel_level", errors_of(CarSerializer(data={**VALID_CAR, "fuel_level": 1.5})))

    def test_unknown_status_rejected(self):
        self.assertIn("status", errors_of(CarSerializer(data={**VALID_CAR, "status": "stolen"})))

    def test_patch_requires_at_least_one_field(self):
        self.assertIn("non_field_errors", errors_of(CarPatchSerializer(data={}, partial=True)))

    def test_patch_keeps_rules_of_full_body(self):
        self.assertIn("year", errors_of(CarPatchSerializer(data={"year": 1985}, partial=True)))

    def test_list_limit_is_capped(self):
        """Ограничение размера страницы — защита от выгрузки всей таблицы одним запросом."""
        self.assertIn("limit", errors_of(CarListQuerySerializer(data={"limit": 1000})))
        self.assertIn("page", errors_of(CarListQuerySerializer(data={"page": 0})))


class CustomerValidationTests(SimpleTestCase):
    """Схема клиента: возраст 18+, формат телефона, лишние поля отбрасываются."""

    def test_valid_customer(self):
        body = CustomerSerializer(data={**VALID_CUSTOMER, "email": "Aigerim@Example.KZ"})
        self.assertTrue(body.is_valid(), body.errors)
        self.assertEqual(body.validated_data["email"], "aigerim@example.kz")

    def test_underage_customer_rejected(self):
        seventeen = date.today().replace(year=date.today().year - 17)
        self.assertIn("birth_date", errors_of(CustomerSerializer(data={**VALID_CUSTOMER, "birth_date": seventeen})))

    def test_phone_format(self):
        for phone in ("8-701-123", "+7 701 123 45 67", "phone"):
            with self.subTest(phone=phone):
                self.assertIn("phone", errors_of(CustomerSerializer(data={**VALID_CUSTOMER, "phone": phone})))

    def test_extra_fields_are_ignored(self):
        """Поле status в теле не принимается: клиент не может сам выставить себе статус."""
        body = CustomerSerializer(data={**VALID_CUSTOMER, "status": "blocked", "id": "x"})
        self.assertTrue(body.is_valid(), body.errors)
        self.assertNotIn("status", body.validated_data)
        self.assertNotIn("id", body.validated_data)


class DriverLicenseValidationTests(SimpleTestCase):
    """Схема ВУ и решения оператора: категория B, срок действия, причина отказа."""

    def test_valid_license_normalized(self):
        body = DriverLicenseSerializer(data={**VALID_LICENSE, "number": "kz0123456", "categories": "b,c"})
        self.assertTrue(body.is_valid(), body.errors)
        self.assertEqual(body.validated_data["number"], "KZ0123456")
        self.assertEqual(body.validated_data["categories"], "B,C")

    def test_category_b_required(self):
        self.assertIn("categories", errors_of(DriverLicenseSerializer(data={**VALID_LICENSE, "categories": "A,BE"})))

    def test_expired_license_rejected(self):
        yesterday = date.today() - timedelta(days=1)
        body = DriverLicenseSerializer(data={**VALID_LICENSE, "issued_on": "2010-01-01", "expires_on": yesterday})
        self.assertIn("expires_on", errors_of(body))

    def test_reject_requires_reason(self):
        self.assertIn("reject_reason", errors_of(LicenseReviewSerializer(data={"status": "rejected"})))

    def test_reason_dropped_on_approval(self):
        body = LicenseReviewSerializer(data={"status": "approved", "reject_reason": "лишнее"})
        self.assertTrue(body.is_valid(), body.errors)
        self.assertEqual(body.validated_data, {"status": "approved"})

    def test_operator_cannot_return_license_to_pending(self):
        self.assertIn("status", errors_of(LicenseReviewSerializer(data={"status": "pending"})))


class BookingValidationTests(SimpleTestCase):
    """Схема брони: тариф и статус из тела не принимаются, car_id — UUID."""

    def test_price_and_status_from_body_are_ignored(self):
        """Тариф и статус брони задаёт сервер; присланные клиентом значения отбрасываются."""
        body = BookingCreateSerializer(data={
            "car_id": "8d1f6c1e-0e4c-4b0a-9d4f-1a2b3c4d5e6f", "price_per_minute": 1, "status": "finished",
            "total_cost": 0,
        })
        self.assertTrue(body.is_valid(), body.errors)
        self.assertEqual(set(body.validated_data), {"car_id"})

    def test_car_id_must_be_uuid(self):
        self.assertIn("car_id", errors_of(BookingCreateSerializer(data={"car_id": "1 OR 1=1"})))


class AccountValidationTests(SimpleTestCase):
    """Схемы регистрации, входа и управления пользователями."""

    def test_email_normalized(self):
        body = RegisterSerializer(data={"email": "  New@Test.KZ ", "password": "Tiger#Moon2026"})
        self.assertTrue(body.is_valid(), body.errors)
        self.assertEqual(body.validated_data["email"], "new@test.kz")

    def test_role_cannot_be_chosen(self):
        body = RegisterSerializer(data={"email": "a@test.kz", "password": "Tiger#Moon2026", "role": "admin"})
        self.assertIn("role", errors_of(body))

    def test_weak_passwords_rejected(self):
        for password in ("short1#", "password123", "1234567890", "a@test.kz1"):
            with self.subTest(password=password):
                self.assertIn("password", errors_of(RegisterSerializer(data={"email": "a@test.kz", "password": password})))

    def test_password_length_capped(self):
        """Пароль длиннее 128 символов отклоняется до хэширования — защита от DoS дорогим Argon2."""
        body = RegisterSerializer(data={"email": "a@test.kz", "password": "Aa#1" * 33})
        self.assertIn("password", errors_of(body))
        self.assertIn("password", errors_of(LoginSerializer(data={"email": "a@test.kz", "password": "Aa#1" * 33})))

    def test_password_whitespace_preserved(self):
        body = LoginSerializer(data={"email": "a@test.kz", "password": " Tiger#Moon2026 "})
        self.assertTrue(body.is_valid(), body.errors)
        self.assertEqual(body.validated_data["password"], " Tiger#Moon2026 ")

    def test_user_patch_needs_field(self):
        self.assertIn("non_field_errors", errors_of(UserPatchSerializer(data={})))
        self.assertIn("role", errors_of(UserPatchSerializer(data={"role": "root"})))

    def test_user_list_limit_capped(self):
        self.assertIn("limit", errors_of(UserListQuerySerializer(data={"limit": 101})))
