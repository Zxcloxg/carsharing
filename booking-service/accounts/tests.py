"""Автотесты ПР4: хранение паролей, регистрация и вход, проверка токена,
роли и права владельца, единый формат ошибок."""
from datetime import datetime, timedelta, timezone
from unittest import mock

import jwt
from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from django.http import HttpResponse
from django.test import override_settings
from django.urls import path
from rest_framework import status
from rest_framework.test import APITestCase

from config import urls as project_urls
from fleet import services as fleet_services
from fleet.tests import VALID_CAR, VALID_CUSTOMER, make_customer

from .models import Role, User
from .testing import TEST_PASSWORD, authenticate, make_account
from .tokens import issue_access_token

REGISTER = "/api/v1/auth/register"
LOGIN = "/api/v1/auth/login"
ME = "/api/v1/auth/me"


def _broken_plain_view(request):
    raise RuntimeError("database password is hunter2")


# Маршрут вне DRF под /api/ — проверка middleware, который отдаёт JSON и на такие сбои.
urlpatterns = [path("api/v1/_broken", _broken_plain_view), *project_urls.urlpatterns]


class AuthTestCase(APITestCase):
    def setUp(self):
        super().setUp()
        cache.clear()  # счётчики ограничения частоты входа не переходят между тестами

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


# --- 1. хранение паролей ------------------------------------------------------
class PasswordStorageTests(AuthTestCase):
    def test_password_is_stored_as_argon2_hash(self):
        self.client.post(REGISTER, {"email": "a@test.kz", "password": TEST_PASSWORD}, format="json")
        stored = User.objects.get(email="a@test.kz").password_hash
        self.assertTrue(stored.startswith("argon2$argon2id$"), stored)
        self.assertNotIn(TEST_PASSWORD, stored)

    def test_same_password_gives_different_hashes(self):
        """Соль у каждого пароля своя: одинаковые пароли не видны по одинаковым хэшам."""
        first = make_account(email="one@test.kz")
        second = make_account(email="two@test.kz")
        hashes = set(User.objects.filter(pk__in=[first["id"], second["id"]]).values_list("password_hash", flat=True))
        self.assertEqual(len(hashes), 2)

    def test_database_rejects_plain_text_password(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            User.objects.create(email="plain@test.kz", password_hash=TEST_PASSWORD)

    def test_database_keeps_email_unique_ignoring_case(self):
        make_account(email="case@test.kz")
        with self.assertRaises(IntegrityError), transaction.atomic():
            user = User(email="CASE@test.kz")
            user.set_password(TEST_PASSWORD)
            user.save()

    def test_password_never_returned(self):
        response = self.client.post(REGISTER, {"email": "b@test.kz", "password": TEST_PASSWORD}, format="json")
        self.assertNotIn("password", response.json())
        self.assertNotIn("password_hash", response.json())


# --- 2. регистрация -----------------------------------------------------------
class RegisterTests(AuthTestCase):
    def _register(self, **body):
        return self.client.post(REGISTER, {"email": "new@test.kz", "password": TEST_PASSWORD, **body}, format="json")

    def test_register_creates_customer(self):
        response = self._register(email="  New@Test.KZ ")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["email"], "new@test.kz")
        self.assertEqual(response.data["role"], "customer")
        self.assertIsNotNone(response.data["created_at"])
        self.assertEqual(response["Location"], f"/api/v1/users/{response.data['id']}")

    def test_duplicate_email_returns_409_even_in_other_case(self):
        self._register()
        error = self.assertError(self._register(email="NEW@test.kz"), 409, "email_taken")
        self.assertEqual(error["details"][0]["field"], "email")
        self.assertEqual(User.objects.count(), 1)

    def test_invalid_email_returns_400(self):
        error = self.assertError(self._register(email="not-an-email"), 400, "validation_error")
        self.assertEqual([d["field"] for d in error["details"]], ["email"])

    def test_short_password_returns_400(self):
        error = self.assertError(self._register(password="Ab1#"), 400, "validation_error")
        self.assertEqual(error["details"][0]["field"], "password")

    def test_common_and_numeric_passwords_rejected(self):
        for weak in ("password123", "1234567890"):
            with self.subTest(password=weak):
                self.assertError(self._register(password=weak), 400, "validation_error")

    def test_password_similar_to_email_rejected(self):
        error = self.assertError(
            self._register(email="aigerim.sapar@test.kz", password="Aigerim.Sapar"), 400, "validation_error"
        )
        self.assertEqual(error["details"][0]["field"], "password")

    def test_demo_passwords_follow_same_rules(self):
        """Пароли демо-пользователей из seed_demo проходят те же проверки, что и при регистрации."""
        from accounts.serializers import RegisterSerializer
        from fleet.management.commands.seed_demo import USERS

        for email, password, _, _ in USERS:
            with self.subTest(email=email):
                body = RegisterSerializer(data={"email": email, "password": password})
                self.assertTrue(body.is_valid(), body.errors)

    def test_missing_fields_listed_in_details(self):
        error = self.assertError(self.client.post(REGISTER, {}, format="json"), 400, "validation_error")
        self.assertEqual({d["field"] for d in error["details"]}, {"email", "password"})

    def test_role_cannot_be_chosen_at_registration(self):
        error = self.assertError(self._register(role="admin"), 400, "validation_error")
        self.assertEqual(error["details"][0]["field"], "role")
        self.assertFalse(User.objects.exists())


# --- 3. вход ------------------------------------------------------------------
class LoginTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.user = make_account(Role.OPERATOR, email="op@test.kz")

    def _login(self, email="op@test.kz", password=TEST_PASSWORD):
        return self.client.post(LOGIN, {"email": email, "password": password}, format="json")

    def test_login_returns_signed_token(self):
        response = self._login(email="OP@test.kz")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["token_type"], "Bearer")
        self.assertEqual(response.data["expires_in"], 3600)
        self.assertEqual(response.data["user"]["role"], "operator")
        self.assertIsNotNone(response.data["user"]["last_login_at"])

        claims = jwt.decode(
            response.data["access_token"], settings.JWT_SECRET, algorithms=["HS256"], issuer="booking-service"
        )
        self.assertEqual(claims["sub"], self.user["id"])
        self.assertEqual(claims["exp"] - claims["iat"], 3600)

    def test_token_from_login_opens_protected_route(self):
        token = self._login().data["access_token"]
        response = self.client.get(ME, HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["email"], "op@test.kz")

    def test_wrong_password_returns_401(self):
        response = self._login(password="Wrong#Passw0rd")
        self.assertError(response, 401, "invalid_credentials")
        self.assertEqual(response["WWW-Authenticate"], 'Bearer realm="api"')

    def test_unknown_email_indistinguishable_from_wrong_password(self):
        unknown = self._login(email="nobody@test.kz").json()
        wrong = self._login(password="Wrong#Passw0rd").json()
        self.assertEqual(unknown, wrong)

    def test_disabled_account_cannot_login(self):
        User.objects.filter(pk=self.user["id"]).update(is_active=False)
        self.assertError(self._login(), 403, "account_disabled")

    def test_missing_password_returns_400(self):
        response = self.client.post(LOGIN, {"email": "op@test.kz"}, format="json")
        self.assertError(response, 400, "validation_error")

    def test_too_many_attempts_return_429(self):
        for _ in range(10):
            self._login(password="Wrong#Passw0rd")
        self.assertError(self._login(), 429, "throttled")


# --- 4. защита маршрутов: проверка токена -------------------------------------
class TokenTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        self.admin = make_account(Role.ADMIN)
        self.car = fleet_services.create_car({**VALID_CAR, "status": "free"})
        self.url = f"/api/v1/cars/{self.car['id']}"

    def _delete_with(self, header):
        return self.client.delete(self.url, HTTP_AUTHORIZATION=header)

    def test_catalog_is_public(self):
        self.assertEqual(self.client.get("/api/v1/cars").status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.get(self.url).status_code, status.HTTP_200_OK)

    def test_no_token_returns_401(self):
        response = self.client.delete(self.url)
        self.assertError(response, 401, "not_authenticated")
        self.assertEqual(response["WWW-Authenticate"], 'Bearer realm="api"')

    def test_wrong_scheme_returns_401(self):
        self.assertError(self._delete_with(f"Token {issue_access_token(self.admin)}"), 401, "invalid_token")

    def test_garbage_token_returns_401(self):
        self.assertError(self._delete_with("Bearer not.a.jwt"), 401, "invalid_token")

    def test_token_with_foreign_signature_returns_401(self):
        payload = jwt.decode(issue_access_token(self.admin), options={"verify_signature": False})
        forged = jwt.encode(payload, "x" * 48, algorithm="HS256")
        self.assertError(self._delete_with(f"Bearer {forged}"), 401, "invalid_token")

    def test_unsigned_token_returns_401(self):
        payload = jwt.decode(issue_access_token(self.admin), options={"verify_signature": False})
        unsigned = jwt.encode(payload, None, algorithm="none")
        self.assertError(self._delete_with(f"Bearer {unsigned}"), 401, "invalid_token")

    def test_expired_token_returns_401(self):
        issued = datetime.now(timezone.utc) - timedelta(hours=2)
        token = issue_access_token(self.admin, now=issued)
        self.assertError(self._delete_with(f"Bearer {token}"), 401, "token_expired")

    def test_token_of_disabled_user_returns_401(self):
        token = issue_access_token(self.admin)
        User.objects.filter(pk=self.admin["id"]).update(is_active=False)
        self.assertError(self._delete_with(f"Bearer {token}"), 401, "account_disabled")

    def test_token_of_deleted_user_returns_401(self):
        token = issue_access_token(self.admin)
        User.objects.filter(pk=self.admin["id"]).delete()
        self.assertError(self._delete_with(f"Bearer {token}"), 401, "invalid_token")

    def test_role_is_taken_from_database_not_from_token(self):
        """Понижение роли действует сразу, не дожидаясь истечения уже выданного токена."""
        token = issue_access_token(self.admin)
        User.objects.filter(pk=self.admin["id"]).update(role=Role.CUSTOMER)
        self.assertError(self._delete_with(f"Bearer {token}"), 403, "permission_denied")

    def test_valid_admin_token_passes(self):
        response = self._delete_with(f"Bearer {issue_access_token(self.admin)}")
        self.assertEqual(response.status_code, status.HTTP_204_NO_CONTENT)


# --- 5. роли ------------------------------------------------------------------
class RoleTests(AuthTestCase):
    """Одна и та же операция от имени анонима, клиента, оператора и администратора."""

    def setUp(self):
        super().setUp()
        self.accounts = {role: make_account(role) for role in Role.values}
        self.customer = make_customer()
        self.license = self.customer.driver_licenses.get()

    def _as(self, role):
        self.client.credentials()
        if role:
            authenticate(self.client, self.accounts[role])

    def _check(self, request, expected):
        for role, code in expected.items():
            with self.subTest(role=role or "anonymous"):
                self._as(role)
                self.assertEqual(request().status_code, code)

    def test_delete_car_only_admin(self):
        cars = iter(
            fleet_services.create_car({**VALID_CAR, "status": "free", "plate": f"00{i}DEL02"}) for i in range(4)
        )
        self._check(lambda: self.client.delete(f"/api/v1/cars/{next(cars)['id']}"), {
            None: 401, "customer": 403, "operator": 403, "admin": 204,
        })

    def test_create_car_only_admin(self):
        plates = iter(f"10{i}NEW02" for i in range(4))
        self._check(lambda: self.client.post("/api/v1/cars", {**VALID_CAR, "plate": next(plates)}, format="json"), {
            None: 401, "customer": 403, "operator": 403, "admin": 201,
        })

    def test_change_car_status_staff_only(self):
        car = fleet_services.create_car({**VALID_CAR, "status": "free"})
        statuses = iter(["service", "service", "service", "free"])
        self._check(
            lambda: self.client.patch(f"/api/v1/cars/{car['id']}", {"status": next(statuses)}, format="json"),
            {None: 401, "customer": 403, "operator": 200, "admin": 200},
        )

    def test_review_license_staff_only(self):
        self.license.status, self.license.reviewed_at = "pending", None
        self.license.save()
        self._check(
            lambda: self.client.patch(
                f"/api/v1/driver-licenses/{self.license.id}", {"status": "approved"}, format="json"
            ),
            {None: 401, "customer": 403, "operator": 200},
        )

    def test_list_customers_staff_only(self):
        self._check(lambda: self.client.get("/api/v1/customers"), {
            None: 401, "customer": 403, "operator": 200, "admin": 200,
        })

    def test_manage_users_only_admin(self):
        self._check(lambda: self.client.get("/api/v1/users"), {
            None: 401, "customer": 403, "operator": 403, "admin": 200,
        })

    def test_403_names_required_role(self):
        self._as("operator")
        error = self.assertError(self.client.delete("/api/v1/cars/whatever"), 403, "permission_denied")
        self.assertIn("admin", error["details"][0]["message"])
        self.assertIn("operator", error["details"][0]["message"])

    def test_admin_promotes_and_blocks_user(self):
        self._as("admin")
        target = self.accounts["customer"]["id"]
        response = self.client.patch(f"/api/v1/users/{target}", {"role": "operator"}, format="json")
        self.assertEqual(response.data["role"], "operator")
        response = self.client.patch(f"/api/v1/users/{target}", {"is_active": False}, format="json")
        self.assertFalse(response.data["is_active"])

    def test_admin_cannot_demote_self(self):
        self._as("admin")
        response = self.client.patch(
            f"/api/v1/users/{self.accounts['admin']['id']}", {"role": "customer"}, format="json"
        )
        self.assertError(response, 409, "cannot_change_own_account")


# --- 5б. права владельца записи -------------------------------------------------
class OwnershipTests(AuthTestCase):
    """Клиент работает только со своими данными, персонал — с любыми."""

    def setUp(self):
        super().setUp()
        self.car = fleet_services.create_car({**VALID_CAR, "status": "free"})
        self.alice = make_customer(phone="+77010000001")
        self.bob = make_customer(phone="+77010000002")
        self.alice_account = make_account(email="alice@test.kz", customer_id=self.alice.id)
        self.bob_account = make_account(email="bob@test.kz", customer_id=self.bob.id)
        self.operator = make_account(Role.OPERATOR)
        authenticate(self.client, self.alice_account)

    def _bob_booking(self):
        return fleet_services.create_booking({"car_id": self.car["id"], "customer_phone": "+77010000002"})

    def test_new_customer_fills_own_profile(self):
        authenticate(self.client, make_account(email="carol@test.kz"))
        response = self.client.post("/api/v1/customers", {**VALID_CUSTOMER, "email": None}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["email"], "carol@test.kz")  # по умолчанию — email учётной записи
        self.assertEqual(self.client.get(ME).data["customer_id"], response.data["id"])

        again = self.client.post("/api/v1/customers", {**VALID_CUSTOMER, "phone": "+77015550000"}, format="json")
        self.assertError(again, 409, "customer_profile_exists")

    def test_customer_reads_only_own_profile(self):
        self.assertEqual(self.client.get(f"/api/v1/customers/{self.alice.id}").status_code, 200)
        self.assertError(self.client.get(f"/api/v1/customers/{self.bob.id}"), 403, "permission_denied")

    def test_customer_cannot_submit_license_for_other(self):
        response = self.client.post(f"/api/v1/customers/{self.bob.id}/driver-licenses", {}, format="json")
        self.assertError(response, 403, "permission_denied")

    def test_customer_books_for_self_without_phone(self):
        response = self.client.post("/api/v1/bookings", {"car_id": self.car["id"]}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["customer_id"], str(self.alice.id))

    def test_customer_cannot_book_for_other(self):
        response = self.client.post(
            "/api/v1/bookings", {"car_id": self.car["id"], "customer_phone": "+77010000002"}, format="json"
        )
        self.assertError(response, 403, "permission_denied")

    def test_customer_without_profile_cannot_book(self):
        authenticate(self.client, make_account(email="noprofile@test.kz"))
        response = self.client.post("/api/v1/bookings", {"car_id": self.car["id"]}, format="json")
        self.assertError(response, 403, "customer_profile_required")

    def test_staff_must_name_customer(self):
        authenticate(self.client, self.operator)
        response = self.client.post("/api/v1/bookings", {"car_id": self.car["id"]}, format="json")
        self.assertError(response, 400, "validation_error")

    def test_customer_sees_only_own_bookings(self):
        self._bob_booking()
        self.assertEqual(self.client.get("/api/v1/bookings").data["total"], 0)
        response = self.client.get(f"/api/v1/bookings?customer_id={self.bob.id}")
        self.assertError(response, 403, "permission_denied")
        authenticate(self.client, self.operator)
        self.assertEqual(self.client.get("/api/v1/bookings").data["total"], 1)

    def test_customer_cannot_touch_other_booking(self):
        booking = self._bob_booking()
        url = f"/api/v1/bookings/{booking['id']}"
        self.assertError(self.client.get(url), 403, "permission_denied")
        self.assertError(self.client.get(f"{url}/payments"), 403, "permission_denied")
        self.assertError(self.client.patch(url, {"status": "cancelled"}, format="json"), 403, "permission_denied")

        authenticate(self.client, self.bob_account)
        self.assertEqual(self.client.patch(url, {"status": "cancelled"}, format="json").status_code, 200)

    def test_operator_closes_any_booking(self):
        booking = self._bob_booking()
        authenticate(self.client, self.operator)
        response = self.client.patch(f"/api/v1/bookings/{booking['id']}", {"status": "finished"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)


# --- 6. единый формат ошибок ----------------------------------------------------
@override_settings(ROOT_URLCONF=__name__)
class ErrorFormatTests(AuthTestCase):
    def setUp(self):
        super().setUp()
        authenticate(self.client, make_account(Role.ADMIN))

    def test_400_validation(self):
        error = self.assertError(self.client.post("/api/v1/cars", {}, format="json"), 400, "validation_error")
        self.assertIn({"field": "plate", "message": "This field is required."}, error["details"])

    def test_400_malformed_json(self):
        response = self.client.post("/api/v1/cars", "{bad json", content_type="application/json")
        self.assertError(response, 400, "parse_error")

    def test_401(self):
        self.client.credentials()
        self.assertError(self.client.get(ME), 401, "not_authenticated")

    def test_403(self):
        authenticate(self.client, make_account(Role.CUSTOMER))
        self.assertError(self.client.get("/api/v1/users"), 403, "permission_denied")

    def test_404_domain(self):
        self.assertError(self.client.get("/api/v1/cars/unknown"), 404, "car_not_found")

    def test_404_unknown_url(self):
        self.assertError(self.client.get("/api/v1/no-such-resource"), 404, "not_found")

    def test_405(self):
        self.assertError(self.client.put("/api/v1/cars", {}, format="json"), 405, "method_not_allowed")

    def test_409(self):
        self.client.post("/api/v1/cars", VALID_CAR, format="json")
        self.assertError(self.client.post("/api/v1/cars", VALID_CAR, format="json"), 409, "plate_already_used")

    def test_415(self):
        response = self.client.post("/api/v1/cars", "plate=1", content_type="text/plain")
        self.assertError(response, 415, "unsupported_media_type")

    def test_500_in_api_view_hides_details(self):
        with mock.patch.object(fleet_services, "list_cars", side_effect=RuntimeError("database password is hunter2")), \
                self.assertLogs("config.exceptions", "ERROR"):
            response = self.client.get("/api/v1/cars")
        self.assertError(response, 500, "internal_error")
        self.assertNotIn(b"hunter2", response.content)
        self.assertNotIn(b"Traceback", response.content)

    def test_500_outside_drf_also_json(self):
        with self.assertLogs("config.middleware", "ERROR"):
            response = self.client.get("/api/v1/_broken")
        self.assertError(response, 500, "internal_error")
        self.assertNotIn(b"hunter2", response.content)
