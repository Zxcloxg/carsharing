"""API-тесты аутентификации: хранение паролей, регистрация, вход, проверка токена (ПР4)."""
from datetime import datetime, timedelta, timezone

import jwt
from django.conf import settings
from django.db import IntegrityError, transaction
from rest_framework import status

from accounts.models import Role, User
from accounts.tokens import issue_access_token
from fleet import services as fleet_services
from tests.helpers import LOGIN, ME, REGISTER, TEST_PASSWORD, VALID_CAR, ApiTestCase, make_account


class PasswordStorageTests(ApiTestCase):
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


class RegisterTests(ApiTestCase):
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


class LoginTests(ApiTestCase):
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

    def test_legacy_hash_upgraded_on_login(self):
        """Хэш старого формата (PBKDF2) при успешном входе прозрачно пересчитывается в Argon2id."""
        from django.contrib.auth.hashers import make_password

        User.objects.filter(pk=self.user["id"]).update(
            password_hash=make_password(TEST_PASSWORD, hasher="pbkdf2_sha256")
        )
        self.assertEqual(self._login().status_code, status.HTTP_200_OK)
        self.assertTrue(User.objects.get(pk=self.user["id"]).password_hash.startswith("argon2$argon2id$"))

    def test_too_many_attempts_return_429(self):
        for _ in range(10):
            self._login(password="Wrong#Passw0rd")
        self.assertError(self._login(), 429, "throttled")


class TokenTests(ApiTestCase):
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
