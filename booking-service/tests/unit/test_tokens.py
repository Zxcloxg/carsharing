"""Модульные тесты выпуска и проверки JWT (accounts/tokens.py) — без базы и HTTP."""
from datetime import datetime, timedelta, timezone

import jwt
from django.conf import settings
from django.test import SimpleTestCase
from rest_framework.exceptions import AuthenticationFailed

from accounts.tokens import decode_access_token, expires_in_seconds, issue_access_token

USER = {"id": "6f1c8a52-3a51-4c43-9a8e-0f4c1d0b7e21", "role": "operator"}


class TokenTests(SimpleTestCase):
    def _payload(self, **overrides):
        now = datetime.now(timezone.utc)
        payload = {"sub": USER["id"], "role": "admin", "iss": settings.JWT_ISSUER, "iat": now,
                   "exp": now + timedelta(minutes=5), **overrides}
        return {k: v for k, v in payload.items() if v is not None}

    def assertRejected(self, token, code):
        with self.assertRaises(AuthenticationFailed) as ctx:
            decode_access_token(token)
        self.assertEqual(ctx.exception.detail.code, code)

    def test_roundtrip(self):
        claims = decode_access_token(issue_access_token(USER))
        self.assertEqual(claims["sub"], USER["id"])
        self.assertEqual(claims["role"], "operator")
        self.assertEqual(claims["iss"], "booking-service")
        self.assertEqual(claims["exp"] - claims["iat"], expires_in_seconds())

    def test_lifetime_from_settings(self):
        self.assertEqual(expires_in_seconds(), int(settings.JWT_ACCESS_TTL.total_seconds()))

    def test_expired_token(self):
        issued = datetime.now(timezone.utc) - settings.JWT_ACCESS_TTL - timedelta(seconds=1)
        self.assertRejected(issue_access_token(USER, now=issued), "token_expired")

    def test_foreign_signature(self):
        forged = jwt.encode(self._payload(), "attacker-key-" + "x" * 40, algorithm="HS256")
        self.assertRejected(forged, "invalid_token")

    def test_alg_none_rejected(self):
        """Классическая атака на JWT: убрать подпись и указать alg=none."""
        self.assertRejected(jwt.encode(self._payload(), None, algorithm="none"), "invalid_token")

    def test_other_issuer_rejected(self):
        token = jwt.encode(self._payload(iss="evil-service"), settings.JWT_SECRET, algorithm="HS256")
        self.assertRejected(token, "invalid_token")

    def test_token_without_expiry_rejected(self):
        """Вечный токен не принимается, даже с правильной подписью."""
        token = jwt.encode(self._payload(exp=None), settings.JWT_SECRET, algorithm="HS256")
        self.assertRejected(token, "invalid_token")

    def test_tampered_payload_rejected(self):
        header, payload, signature = issue_access_token(USER).split(".")
        other = issue_access_token({**USER, "role": "admin"}).split(".")[1]
        self.assertRejected(f"{header}.{other}.{signature}", "invalid_token")

    def test_garbage(self):
        self.assertRejected("not.a.jwt", "invalid_token")
