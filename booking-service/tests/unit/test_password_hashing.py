"""Модульные тесты хэширования паролей: рабочие настройки и свойства хэша — без базы."""
from django.contrib.auth.hashers import check_password, identify_hasher, make_password
from django.test import SimpleTestCase

import config.settings as production


class ProductionHashingSettingsTests(SimpleTestCase):
    """Рабочие настройки: Argon2id первым, слабых хэшеров нет."""

    def test_argon2id_with_default_cost_is_primary(self):
        """В рабочих настройках новые пароли хэширует Argon2id с параметрами Django по умолчанию."""
        self.assertEqual(production.PASSWORD_HASHERS[0], "django.contrib.auth.hashers.Argon2PasswordHasher")

    def test_no_weak_hashers_enabled(self):
        """Хэшеры без соли или с быстрым однократным хэшем не подключены даже для старых паролей."""
        weak = {"MD5PasswordHasher", "UnsaltedSHA1PasswordHasher", "UnsaltedMD5PasswordHasher",
                "CryptPasswordHasher", "SHA1PasswordHasher"}
        for path in production.PASSWORD_HASHERS:
            with self.subTest(hasher=path):
                self.assertNotIn(path.rsplit(".", 1)[1], weak)


class HashPropertiesTests(SimpleTestCase):
    """Хэш в формате Argon2id, соль делает хэши разными, проверка пароля."""

    def test_hash_is_argon2id(self):
        encoded = make_password("Tiger#Moon2026")
        self.assertTrue(encoded.startswith("argon2$argon2id$"), encoded)
        self.assertEqual(identify_hasher(encoded).algorithm, "argon2")

    def test_salt_makes_hashes_unique(self):
        self.assertNotEqual(make_password("Tiger#Moon2026"), make_password("Tiger#Moon2026"))

    def test_check_password(self):
        encoded = make_password("Tiger#Moon2026")
        self.assertTrue(check_password("Tiger#Moon2026", encoded))
        self.assertFalse(check_password("tiger#moon2026", encoded))
        self.assertFalse(check_password("", encoded))
