"""Ограничение частоты запросов на входе и регистрации (OWASP API4:2023, API2:2023).

Лимит по умолчанию — 10 запросов в минуту с одного IP (LOGIN_THROTTLE_RATE,
REGISTER_THROTTLE_RATE). Главная проверка ПР5: лимит нельзя обойти,
подставляя в каждый запрос новый заголовок X-Forwarded-For.
"""
from django.conf import settings
from django.test import override_settings
from rest_framework.settings import api_settings

from accounts.models import Role
from tests.helpers import LOGIN, REGISTER, TEST_PASSWORD, ApiTestCase, authenticate, make_account

LIMIT = 10


class LoginRateLimitTests(ApiTestCase):
    """Лимит попыток входа: 429 после 10 попыток, обход через X-Forwarded-For и чужой токен не работает."""

    def setUp(self):
        super().setUp()
        make_account(Role.ADMIN, email="admin@test.kz")

    def _guess(self, n, **extra):
        return self.client.post(LOGIN, {"email": "admin@test.kz", "password": f"Guess#{n:04d}"}, format="json", **extra)

    def test_eleventh_attempt_is_throttled(self):
        codes = [self._guess(n).status_code for n in range(LIMIT)]
        self.assertEqual(codes, [401] * LIMIT)
        response = self._guess(LIMIT)
        self.assertError(response, 429, "throttled")
        self.assertGreater(int(response["Retry-After"]), 0)

    def test_correct_password_is_also_blocked_after_limit(self):
        """Иначе перебор продолжался бы: 429 на неверный пароль и 200 на верный выдали бы ответ."""
        for n in range(LIMIT):
            self._guess(n)
        response = self.client.post(LOGIN, {"email": "admin@test.kz", "password": TEST_PASSWORD}, format="json")
        self.assertError(response, 429, "throttled")

    def test_spoofed_x_forwarded_for_does_not_reset_limit(self):
        """Злоумышленник подставляет в каждый запрос новый X-Forwarded-For, но IP-то один.

        До исправления (NUM_PROXIES не задан) DRF брал клиента из этого
        заголовка, и все 11+ попыток получали 401: перебор паролей без ограничений.
        """
        codes = [self._guess(n, HTTP_X_FORWARDED_FOR=f"203.0.113.{n}").status_code for n in range(LIMIT + 2)]
        self.assertEqual(codes, [401] * LIMIT + [429, 429])

    def test_limit_is_per_client_address(self):
        for n in range(LIMIT):
            self._guess(n, REMOTE_ADDR="198.51.100.1")
        self.assertEqual(self._guess(99, REMOTE_ADDR="198.51.100.1").status_code, 429)
        self.assertEqual(self._guess(99, REMOTE_ADDR="198.51.100.2").status_code, 401)

    def test_behind_gateway_real_client_taken_from_last_hop(self):
        """За API Gateway (TRUSTED_PROXY_COUNT=1) клиент — адрес, который дописал сам шлюз, а не присланный."""
        rest = {**settings.REST_FRAMEWORK, "NUM_PROXIES": 1}
        with override_settings(REST_FRAMEWORK=rest):
            self.assertEqual(api_settings.NUM_PROXIES, 1)
            codes = [
                self._guess(n, HTTP_X_FORWARDED_FOR=f"203.0.113.{n}, 198.51.100.7").status_code
                for n in range(LIMIT + 1)
            ]
        self.assertEqual(codes, [401] * LIMIT + [429])


    def test_own_token_does_not_give_separate_limit(self):
        """С токеном своей учётной записи попытки по-прежнему считаются по адресу, а не по пользователю."""
        for n in range(LIMIT):
            self._guess(n)
        authenticate(self.client, make_account(email="attacker@test.kz"))
        self.assertError(self._guess(LIMIT), 429, "throttled")


class RegisterRateLimitTests(ApiTestCase):
    """Лимит регистраций с одного адреса."""

    def test_mass_registration_is_throttled(self):
        for n in range(LIMIT):
            self.client.post(REGISTER, {"email": f"bot{n}@test.kz", "password": TEST_PASSWORD}, format="json")
        response = self.client.post(REGISTER, {"email": "bot99@test.kz", "password": TEST_PASSWORD}, format="json")
        self.assertError(response, 429, "throttled")

    def test_spoofed_x_forwarded_for_does_not_reset_limit(self):
        codes = [
            self.client.post(REGISTER, {}, format="json", HTTP_X_FORWARDED_FOR=f"203.0.113.{n}").status_code
            for n in range(LIMIT + 1)
        ]
        self.assertEqual(codes, [400] * LIMIT + [429])


class ThrottleSettingsTests(ApiTestCase):
    """Число доверенных прокси задано явно."""

    def test_proxy_count_is_configured(self):
        """NUM_PROXIES=None означает «верить X-Forwarded-For целиком» — так оставлять нельзя."""
        self.assertIsNotNone(api_settings.NUM_PROXIES)
        self.assertGreaterEqual(api_settings.NUM_PROXIES, 0)
