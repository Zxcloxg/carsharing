"""Запуск автотестов (TEST_RUNNER в config/settings.py).

Отличий от стандартного DiscoverRunner два. Первое: пароли в тестах хэшируются тем
же Argon2id, но с облегчёнными параметрами — 1 МБ памяти и один проход
вместо 100 МБ и двух. Сотни регистраций и входов в тестах не упираются в
свободную память машины и идут в разы быстрее; формат хэша
(argon2$argon2id$...) и логика проверки пароля те же. Рабочие параметры
хэширования проверяет отдельный тест (tests/unit/test_password_hashing.py).

Второе (ПР6): информационные сообщения очереди и платежей (логгеры jobs и
fleet.payments) в отчёт тестов не выводятся — их сотни. Тесты, которым лог
важен, проверяют его через assertLogs: он сам временно включает нужный уровень.
"""
import logging

from django.contrib.auth.hashers import Argon2PasswordHasher
from django.test.runner import DiscoverRunner
from django.test.utils import override_settings


class LightArgon2PasswordHasher(Argon2PasswordHasher):
    time_cost = 1
    memory_cost = 1024  # КиБ
    parallelism = 1


class TestRunner(DiscoverRunner):
    def setup_test_environment(self, **kwargs):
        super().setup_test_environment(**kwargs)
        # override_settings рассылает setting_changed — Django сбрасывает кэш хэшеров.
        self._hashers = override_settings(PASSWORD_HASHERS=[
            "tests.runner.LightArgon2PasswordHasher",
            "django.contrib.auth.hashers.PBKDF2PasswordHasher",
        ])
        self._hashers.enable()
        self._quiet = {name: logging.getLogger(name).level for name in ("jobs", "fleet.payments")}
        for name in self._quiet:
            logging.getLogger(name).setLevel(logging.WARNING)

    def teardown_test_environment(self, **kwargs):
        self._hashers.disable()
        for name, level in self._quiet.items():
            logging.getLogger(name).setLevel(level)
        super().teardown_test_environment(**kwargs)
