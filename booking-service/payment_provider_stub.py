"""Эмулятор платёжного провайдера для локального запуска и демонстрации (ПР6).

Отдельный HTTP-сервис на стандартной библиотеке Python — та внешняя
зависимость, к которой booking-service обращается из фоновой задачи списания:

    POST /v1/charges   {"amount": "240.00", "currency": "KZT", "customer_ref": "...", "description": "..."}
                       заголовок Idempotency-Key: <id платежа>
    -> 201 {"id": "ch_…", "status": "succeeded", "amount": "240.00", "currency": "KZT"}
    GET  /health       -> 200 {"status": "ok"}

Поведение настраивается ключами — чтобы показать и штатный путь, и сбои:
    --latency 1.5      задержка ответа, с (банк отвечает не мгновенно)
    --fail-first 2     первые N списаний отвечают 503 (провайдер перегружен)
    --decline-over 5000  суммы больше указанной отклоняются с 402 (недостаточно средств)

Повтор с тем же Idempotency-Key возвращает уже созданное списание, а не
списывает деньги второй раз.

Запуск: python payment_provider_stub.py [--port 8030] (Windows: payment_provider.cmd).
"""
import argparse
import json
import threading
import time
import uuid
from decimal import Decimal, InvalidOperation
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

charges = {}  # Idempotency-Key -> созданное списание
lock = threading.Lock()
state = {"requests": 0}


def log(message):
    print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] provider: {message}", flush=True)


class Handler(BaseHTTPRequestHandler):
    options = None

    def log_message(self, *args):  # стандартный лог http.server заменён своим, короче
        pass

    def _reply(self, code, body):
        raw = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        try:
            self.wfile.write(raw)
        except ConnectionError:  # клиент не дождался ответа (тайм-аут) и закрыл соединение
            log("клиент закрыл соединение, не дождавшись ответа")

    def do_GET(self):
        if self.path == "/health":
            self._reply(200, {"status": "ok", "service": "payment-provider-stub"})
        else:
            self._reply(404, {"error": {"message": "Not found"}})

    def do_POST(self):
        if self.path != "/v1/charges":
            self._reply(404, {"error": {"message": "Not found"}})
            return
        key = self.headers.get("Idempotency-Key")
        try:
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
            amount = Decimal(str(body["amount"]))
        except (ValueError, KeyError, InvalidOperation):
            self._reply(400, {"error": {"message": "amount is required"}})
            return
        if not key:
            self._reply(400, {"error": {"message": "Idempotency-Key header is required"}})
            return

        with lock:
            state["requests"] += 1
            number = state["requests"]
            existing = charges.get(key)
        if existing:
            log(f"#{number} списание {key[:8]} уже выполнено ранее -> {existing['id']} (повтор по ключу идемпотентности)")
            self._reply(200, existing)
            return

        time.sleep(self.options.latency)
        if number <= self.options.fail_first:
            log(f"#{number} списание {key[:8]} на {amount} ₸ -> 503 (имитация перегрузки)")
            self._reply(503, {"error": {"message": "Service temporarily unavailable"}})
            return
        if self.options.decline_over is not None and amount > self.options.decline_over:
            log(f"#{number} списание {key[:8]} на {amount} ₸ -> 402 (недостаточно средств)")
            self._reply(402, {"error": {"message": "Insufficient funds"}})
            return

        charge = {
            "id": f"ch_{uuid.uuid4().hex[:20]}",
            "status": "succeeded",
            "amount": str(amount),
            "currency": body.get("currency", "KZT"),
        }
        with lock:
            charge = charges.setdefault(key, charge)
        log(f"#{number} списание {key[:8]} на {amount} ₸ -> 201 {charge['id']} (за {self.options.latency:g} с)")
        self._reply(201, charge)


def main():
    parser = argparse.ArgumentParser(description="Эмулятор платёжного провайдера")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8030)
    parser.add_argument("--latency", type=float, default=1.5, help="Задержка ответа на списание, с")
    parser.add_argument("--fail-first", type=int, default=0, help="Сколько первых списаний ответить 503")
    parser.add_argument("--decline-over", type=Decimal, default=None, help="Отклонять (402) суммы больше этой")
    Handler.options = parser.parse_args()
    server = ThreadingHTTPServer((Handler.options.host, Handler.options.port), Handler)
    log(
        f"слушаю http://{Handler.options.host}:{Handler.options.port}, задержка {Handler.options.latency:g} с, "
        f"503 на первые {Handler.options.fail_first}, отказ свыше {Handler.options.decline_over or '—'}"
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log("остановлен")


if __name__ == "__main__":
    main()
