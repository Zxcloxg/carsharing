"""Клиент платёжного провайдера (эквайринга).

Списание за поездку — HTTP-вызов внешнего сервиса: POST {PAYMENT_PROVIDER_URL}/v1/charges.
Вызов идёт только из фоновой задачи (fleet/tasks.py), никогда из запроса к API.

Ошибки разделены на два вида, от этого зависит, повторит ли задачу обработчик:
* PaymentProviderUnavailable — временная: провайдер не отвечает, тайм-аут,
  ответ 5xx или 429. Задача повторяется с паузой;
* PaymentDeclined — окончательная: банк отказал (402) или отклонил запрос
  (прочие 4xx). Повтор вернёт тот же отказ, поэтому задача завершается сразу.

Каждый вызов несёт заголовок Idempotency-Key = id платежа. Если провайдер
списал деньги, а ответ потерялся (тайм-аут), повторный вызов с тем же ключом
вернёт уже созданное списание, а не спишет деньги второй раз.
"""
import json
import logging
import socket
import time
import urllib.error
import urllib.request

from django.conf import settings

from jobs.retry import PermanentTaskError

logger = logging.getLogger("fleet.payments")

# Без системного прокси: провайдер вызывается напрямую по адресу из настроек.
_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class PaymentProviderUnavailable(Exception):
    """Провайдер недоступен или перегружен — имеет смысл повторить позже."""


class PaymentDeclined(PermanentTaskError):
    """Провайдер отказал в списании — повтор не поможет."""


def charge(*, payment_id, amount, customer_id, description):
    """Списать `amount` тенге; вернуть идентификатор операции у провайдера."""
    url = settings.PAYMENT_PROVIDER_URL.rstrip("/") + "/v1/charges"
    body = json.dumps({
        "amount": str(amount),
        "currency": "KZT",
        "customer_ref": str(customer_id),
        "description": description,
    }).encode()
    request = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": "application/json",
        "Idempotency-Key": str(payment_id),
    })
    timeout = settings.PAYMENT_PROVIDER_TIMEOUT
    logger.info("Платёж %s: запрос к провайдеру %s, сумма %s ₸, тайм-аут %g с", str(payment_id)[:8], url, amount, timeout)
    started = time.monotonic()
    try:
        with _opener.open(request, timeout=timeout) as response:
            data = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as exc:
        detail = _error_text(exc)
        if exc.code == 429 or exc.code >= 500:
            raise PaymentProviderUnavailable(f"Provider answered {exc.code}: {detail}") from exc
        raise PaymentDeclined(f"Provider declined with {exc.code}: {detail}") from exc
    except (urllib.error.URLError, TimeoutError, socket.timeout, ConnectionError) as exc:
        reason = getattr(exc, "reason", exc)
        raise PaymentProviderUnavailable(f"Provider is unreachable at {url}: {reason}") from exc
    except ValueError as exc:  # не JSON — сломанный ответ, считаем временным сбоем
        raise PaymentProviderUnavailable(f"Provider returned invalid JSON: {exc}") from exc

    provider_id = data.get("id")
    if data.get("status") != "succeeded" or not provider_id:
        raise PaymentProviderUnavailable(f"Unexpected provider response: {data}")
    logger.info(
        "Платёж %s: провайдер подтвердил списание %s за %.2f с", str(payment_id)[:8], provider_id, time.monotonic() - started
    )
    return provider_id


def _error_text(exc):
    try:
        data = json.loads(exc.read() or b"{}")
        return data.get("error", {}).get("message") or data.get("message") or exc.reason
    except (ValueError, AttributeError):
        return exc.reason
