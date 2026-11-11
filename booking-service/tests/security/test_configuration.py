"""Конфигурация сервиса и инвентаризация API (OWASP API8:2023, API9:2023).

* Режим отладки и секретный ключ Django берутся из окружения; по умолчанию
  отладка выключена, а ключ не лежит в репозитории.
* Ответы несут защитные заголовки.
* Каждый маршрут описан в контракте openapi.yaml, и в контракте нет
  маршрутов, которых нет в коде, — «теневых» или забытых эндпоинтов нет.
"""
import importlib
import os
import re
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import yaml
from django.conf import settings
from django.test import SimpleTestCase

from tests.helpers import ApiTestCase

from .routes import api_routes


class DjangoSettingsTests(SimpleTestCase):
    def _settings_with(self, **env):
        """Модуль настроек, заново прочитанный с заданным окружением (без файла .env)."""
        import config.settings as module

        base = {k: v for k, v in os.environ.items() if not k.startswith("DJANGO_")}
        try:
            with mock.patch.dict(os.environ, {**base, **env}, clear=True), mock.patch("dotenv.load_dotenv"):
                reloaded = importlib.reload(module)
                return SimpleNamespace(DEBUG=reloaded.DEBUG, ALLOWED_HOSTS=reloaded.ALLOWED_HOSTS)
        finally:
            importlib.reload(module)  # вернуть настройки, прочитанные из настоящего окружения

    def test_debug_off_by_default(self):
        self.assertFalse(self._settings_with(DJANGO_SECRET_KEY="k" * 50).DEBUG)

    def test_debug_only_on_explicit_request(self):
        self.assertTrue(self._settings_with(DJANGO_SECRET_KEY="k" * 50, DJANGO_DEBUG="true").DEBUG)
        self.assertFalse(self._settings_with(DJANGO_SECRET_KEY="k" * 50, DJANGO_DEBUG="no").DEBUG)

    def test_service_refuses_to_start_without_strong_secret_key(self):
        from django.core.exceptions import ImproperlyConfigured

        for env in ({}, {"DJANGO_SECRET_KEY": "short"}):
            with self.subTest(env=env), self.assertRaises(ImproperlyConfigured):
                self._settings_with(**env)

    def test_secret_key_not_in_repository(self):
        source = (Path(settings.BASE_DIR) / "config" / "settings.py").read_text(encoding="utf-8")
        self.assertNotIn("django-insecure", source)
        self.assertNotRegex(source, re.compile(r"^SECRET_KEY\s*=\s*['\"]", re.MULTILINE))

    def test_secret_key_strong(self):
        self.assertGreaterEqual(len(settings.SECRET_KEY), 50)
        self.assertFalse(settings.SECRET_KEY.startswith("django-insecure"))

    def test_hosts_restricted(self):
        self.assertNotIn("*", settings.ALLOWED_HOSTS)


class SecurityHeadersTests(ApiTestCase):
    def test_api_response_headers(self):
        response = self.client.get("/api/v1/cars")
        self.assertEqual(response["Content-Type"], "application/json")
        self.assertEqual(response["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response["X-Frame-Options"], "DENY")
        self.assertEqual(response["Referrer-Policy"], "same-origin")


class ApiInventoryTests(SimpleTestCase):
    def test_contract_matches_code(self):
        spec = yaml.safe_load((Path(settings.BASE_DIR) / "openapi.yaml").read_text(encoding="utf-8"))
        documented = {
            (method.upper(), re.sub(r"\{[^}]+\}", "{id}", path))
            for path, operations in spec["paths"].items() if path.startswith("/api/")
            for method in operations if method in ("get", "post", "put", "patch", "delete")
        }
        self.assertEqual(api_routes(), documented)


class EnvFlagTests(SimpleTestCase):
    def test_parsing(self):
        from config.settings import env_flag

        cases = {"1": True, "true": True, "TRUE ": True, "yes": True, "on": True,
                 "0": False, "false": False, "no": False, "": False, "debug": False}
        for raw, expected in cases.items():
            with self.subTest(raw=raw), mock.patch.dict(os.environ, {"X_FLAG": raw}):
                self.assertIs(env_flag("X_FLAG"), expected)
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assertFalse(env_flag("X_FLAG"))
            self.assertTrue(env_flag("X_FLAG", default=True))
