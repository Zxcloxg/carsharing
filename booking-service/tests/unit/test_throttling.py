"""Модульные тесты определения клиента для ограничения частоты (accounts/throttling.py) — без базы и HTTP."""
from types import SimpleNamespace

from django.conf import settings
from django.test import SimpleTestCase, override_settings
from rest_framework.test import APIRequestFactory

from accounts.authentication import CurrentUser
from accounts.throttling import ClientAddressThrottle

factory = APIRequestFactory()
VIEW = SimpleNamespace(throttle_scope="login")


def key_for(user=None, **meta):
    django_request = factory.post("/api/v1/auth/login", **meta)
    request = SimpleNamespace(user=user, META=django_request.META, headers=django_request.headers)
    throttle = ClientAddressThrottle()
    throttle.scope = "login"
    return throttle.get_cache_key(request, VIEW)


class ClientAddressTests(SimpleTestCase):
    def test_key_is_remote_address(self):
        self.assertEqual(key_for(REMOTE_ADDR="198.51.100.1"), "throttle_login_198.51.100.1")

    def test_client_supplied_forwarded_for_is_ignored(self):
        """Без доверенного прокси (TRUSTED_PROXY_COUNT=0) X-Forwarded-For от клиента не влияет на ключ."""
        spoofed = key_for(REMOTE_ADDR="198.51.100.1", HTTP_X_FORWARDED_FOR="203.0.113.9")
        self.assertEqual(spoofed, "throttle_login_198.51.100.1")

    def test_last_hop_used_behind_gateway(self):
        with override_settings(REST_FRAMEWORK={**settings.REST_FRAMEWORK, "NUM_PROXIES": 1}):
            key = key_for(REMOTE_ADDR="10.0.0.5", HTTP_X_FORWARDED_FOR="203.0.113.9, 198.51.100.7")
        self.assertEqual(key, "throttle_login_198.51.100.7")

    def test_token_owner_does_not_change_key(self):
        user = CurrentUser("u1", "a@test.kz", "customer", None)
        self.assertEqual(key_for(user, REMOTE_ADDR="198.51.100.1"), key_for(None, REMOTE_ADDR="198.51.100.1"))

    def test_current_user_has_pk(self):
        self.assertEqual(CurrentUser("u1", "a@test.kz", "customer", None).pk, "u1")
