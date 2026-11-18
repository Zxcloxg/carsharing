"""Интеграция с платёжным провайдером: настоящий HTTP-вызов эмулятора (payment_provider_stub.py).

Эмулятор запускается в потоке на свободном порту; проверяется весь путь
fleet.payment_gateway.charge — запрос, заголовок идемпотентности, тайм-аут и
разбор ответов 201 / 402 / 503 / «нет соединения».
"""
import socket
import time
import threading
from argparse import Namespace
from decimal import Decimal
from http.server import ThreadingHTTPServer
from unittest import mock

from django.test import SimpleTestCase, override_settings

import payment_provider_stub as stub
from fleet import payment_gateway
from fleet.payment_gateway import PaymentDeclined, PaymentProviderUnavailable


def free_port():
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


class ProviderStubMixin:
    latency = 0.0
    fail_first = 0
    decline_over = Decimal("5000")

    def setUp(self):
        super().setUp()
        stub.charges.clear()
        stub.state["requests"] = 0
        quiet = mock.patch.object(stub, "log")  # без вывода эмулятора в отчёт тестов
        quiet.start()
        self.addCleanup(quiet.stop)
        handler = type("Handler", (stub.Handler,), {"options": Namespace(
            latency=self.latency, fail_first=self.fail_first, decline_over=self.decline_over,
        )})
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.addCleanup(self.server.server_close)
        self.addCleanup(self.server.shutdown)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}"

    def charge(self, payment_id="pay-1", amount="240.00", **settings_overrides):
        with override_settings(PAYMENT_PROVIDER_URL=self.url, **settings_overrides):
            return payment_gateway.charge(payment_id=payment_id, amount=amount, customer_id="c-1", description="test")


class SuccessfulChargeTests(ProviderStubMixin, SimpleTestCase):
    def test_returns_provider_operation_id(self):
        with self.assertLogs("fleet.payments", "INFO") as logs:
            provider_id = self.charge()
        self.assertTrue(provider_id.startswith("ch_"))
        self.assertIn("провайдер подтвердил списание", "\n".join(logs.output))

    def test_same_idempotency_key_does_not_charge_twice(self):
        """Повтор после потерянного ответа: тот же ключ — та же операция, второго списания нет."""
        first = self.charge(payment_id="pay-1")
        second = self.charge(payment_id="pay-1")
        self.assertEqual(first, second)
        self.assertEqual(len(stub.charges), 1)
        self.assertNotEqual(self.charge(payment_id="pay-2"), first)


class FailingChargeTests(ProviderStubMixin, SimpleTestCase):
    fail_first = 1

    def test_503_is_temporary_and_next_call_succeeds(self):
        with self.assertRaisesMessage(PaymentProviderUnavailable, "Provider answered 503"):
            self.charge()
        self.assertTrue(self.charge().startswith("ch_"))

    def test_402_is_permanent_decline(self):
        with self.assertRaises(PaymentProviderUnavailable):
            self.charge()  # первый вызов эмулятор отклоняет с 503
        with self.assertRaisesMessage(PaymentDeclined, "Insufficient funds"):
            self.charge(payment_id="pay-big", amount="9000.00")


class SlowProviderTests(ProviderStubMixin, SimpleTestCase):
    latency = 0.5

    def test_timeout_is_temporary_failure(self):
        """Провайдер отвечает дольше тайм-аута — вызов прерывается, а не висит."""
        with self.assertRaisesMessage(PaymentProviderUnavailable, "unreachable"):
            self.charge(PAYMENT_PROVIDER_TIMEOUT=0.1)
        time.sleep(self.latency)  # дать эмулятору дописать ответ до остановки сервера


class UnreachableProviderTests(SimpleTestCase):
    def test_connection_refused_is_temporary_failure(self):
        """Провайдер выключен: на порту никто не слушает."""
        with override_settings(PAYMENT_PROVIDER_URL=f"http://127.0.0.1:{free_port()}"):
            with self.assertRaisesMessage(PaymentProviderUnavailable, "unreachable"):
                payment_gateway.charge(payment_id="p", amount="1", customer_id="c", description="d")


class MalformedResponseTests(SimpleTestCase):
    """Сломанный ответ провайдера — временный сбой, а не падение задачи с непонятной ошибкой."""

    def _respond(self, raw):
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = raw
        return mock.patch.object(payment_gateway._opener, "open", return_value=response)

    def test_invalid_json(self):
        with self._respond(b"<html>"), self.assertRaisesMessage(PaymentProviderUnavailable, "invalid JSON"):
            payment_gateway.charge(payment_id="p", amount="1", customer_id="c", description="d")

    def test_unexpected_status(self):
        with self._respond(b'{"status": "processing"}'),                 self.assertRaisesMessage(PaymentProviderUnavailable, "Unexpected provider response"):
            payment_gateway.charge(payment_id="p", amount="1", customer_id="c", description="d")

    def test_error_body_without_json_uses_http_reason(self):
        import urllib.error
        from io import BytesIO

        error = urllib.error.HTTPError("http://x", 400, "Bad Request", {}, BytesIO(b"oops"))
        with mock.patch.object(payment_gateway._opener, "open", side_effect=error),                 self.assertRaisesMessage(PaymentDeclined, "declined with 400: Bad Request"):
            payment_gateway.charge(payment_id="p", amount="1", customer_id="c", description="d")
