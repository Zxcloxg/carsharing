"""Проверка фонового списания на живых процессах (ПР6) и протокол docs/async-scenario.md.

Скрипт сам запускает три процесса — API (runserver 8010), обработчик очереди
(run_worker) и эмулятор платёжного провайдера (payment_provider_stub.py, порт
8030), — и проходит сценарий дважды:

1. Штатный путь: клиент завершает поездку -> ответ сразу, платёж pending ->
   обработчик списывает деньги -> платёж paid, чек отправлен.
2. Путь со сбоем: провайдер выключен -> поездка всё равно закрывается сразу ->
   обработчик делает 3 попытки с паузами 2 и 4 с, причины пишет в лог ->
   задача FAILED, платёж failed с причиной; API всё это время отвечает.
   Затем провайдер поднимается (первое списание отвечает 503), клиент
   повторяет оплату -> 202 Accepted -> со второй попытки платёж paid.

Логи всех процессов сохраняются, нужные фрагменты попадают в протокол.

Перед запуском: PostgreSQL запущен, python manage.py migrate и seed_demo
выполнены, порты 8010 и 8030 свободны. Запуск: async_check.cmd
(или python async_check.py [--out путь]).
"""
import argparse
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
PYTHON = sys.executable
API = "http://127.0.0.1:8010"
PROVIDER = "http://127.0.0.1:8030"
DEFAULT_PROTOCOL = HERE.parent / "docs" / "async-scenario.md"
LOG_DIR = HERE / ".async-check"

CUSTOMER = ("daniyar@example.kz", "River#Stone2026")
OPERATOR = ("operator@carsharing.kz", "Orbit#Signal2026")
ADMIN = ("admin@carsharing.kz", "Steppe#Falcon2026")
PROVIDER_LATENCY = 1.5

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


# --- HTTP -------------------------------------------------------------------
class Reply:
    def __init__(self, code, raw, headers, elapsed):
        self.code, self.headers, self.elapsed = code, headers, elapsed
        self.data = json.loads(raw) if raw else None


def call(method, path, body=None, token=None, base=API, timeout=15):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    started = time.perf_counter()
    try:
        with opener.open(req, timeout=timeout) as response:
            return Reply(response.status, response.read(), response.headers, time.perf_counter() - started)
    except urllib.error.HTTPError as exc:
        return Reply(exc.code, exc.read(), exc.headers, time.perf_counter() - started)


def login(email, password):
    reply = call("POST", "/api/v1/auth/login", {"email": email, "password": password})
    if reply.code != 200:
        sys.exit(f"Вход {email} не удался ({reply.code}): выполните python manage.py seed_demo")
    return reply.data["access_token"]


def wait_until_up(url, what, seconds=30):
    deadline = time.time() + seconds
    while time.time() < deadline:
        try:
            with opener.open(url, timeout=2):
                return
        except (urllib.error.URLError, ConnectionError, TimeoutError):
            time.sleep(0.3)
    sys.exit(f"{what} не поднялся за {seconds} с — см. логи в {LOG_DIR}")


# --- процессы ---------------------------------------------------------------
class Process:
    def __init__(self, name, args, env=None):
        self.name, self.args, self.env = name, args, env
        self.log_path = LOG_DIR / f"{name}.log"
        self.proc = None

    def start(self, *extra):
        child_env = {**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUNBUFFERED": "1", **(self.env or {})}
        log = open(self.log_path, "a", encoding="utf-8")
        self.proc = subprocess.Popen(
            [PYTHON, *self.args, *extra], cwd=HERE, env=child_env, stdout=log, stderr=subprocess.STDOUT,
        )

    def stop(self):
        if self.proc and self.proc.poll() is None:
            self.proc.terminate()  # жёсткая остановка — как падение процесса
            self.proc.wait(timeout=10)

    def mark(self):
        return self.log_path.stat().st_size if self.log_path.exists() else 0

    def since(self, offset):
        with open(self.log_path, encoding="utf-8") as f:
            f.seek(offset)
            return [line.rstrip() for line in f if line.strip()]


# --- протокол ---------------------------------------------------------------
class Protocol:
    def __init__(self):
        self.lines = []

    def add(self, *lines):
        self.lines.extend(lines)

    def code(self, text, lang=""):
        self.add(f"```{lang}", *text.rstrip().splitlines(), "```", "")

    def json(self, data):
        self.code(json.dumps(data, ensure_ascii=False, indent=2), "json")


def short(value):
    return value[:8] if value else "—"


def poll_payment(payment_id, token, until, limit_seconds, started):
    """Опрос GET /payments/{id}; строки таблицы «время — статус платежа и задачи» без повторов."""
    rows, last = [], None
    deadline = time.perf_counter() + limit_seconds
    while time.perf_counter() < deadline:
        reply = call("GET", f"/api/v1/payments/{payment_id}", token=token)
        p, t = reply.data, reply.data["task"]
        state = (p["status"], t["status"], t["attempts"], t["last_error"])
        if state != last:
            rows.append((time.perf_counter() - started, p, t, reply.elapsed))
            last = state
        if until(p, t):
            return rows, p
        time.sleep(0.2)
    sys.exit(f"Платёж {payment_id} не дошёл до конечного состояния за {limit_seconds} с")


def timeline_table(rows):
    out = ["| t, с | Платёж | Задача | Попытка | Следующая попытка | Последняя ошибка | Ответ API, мс |",
           "|---|---|---|---|---|---|---|"]
    for t, p, task, elapsed in rows:
        nxt = task["next_attempt_at"][11:19] if task["next_attempt_at"] else "—"
        err = (task["last_error"] or "—").replace("|", "\\|")
        if len(err) > 90:
            err = err[:87] + "…"
        out.append(f"| {t:.1f} | `{p['status']}` | `{task['status']}` | {task['attempts']}/{task['max_attempts']} "
                   f"| {nxt} | {err} | {elapsed * 1000:.0f} |")
    return out + [""]


def pick_free_car(token):
    cars = call("GET", "/api/v1/cars?status=free&limit=100", token=token).data["items"]
    if not cars:
        sys.exit("Нет свободных машин: выполните seed_demo на чистой базе")
    return cars[0]


def finish_trip(token, protocol, label):
    car = pick_free_car(token)
    booking = call("POST", "/api/v1/bookings", {"car_id": car["id"]}, token=token)
    if booking.code != 201:
        sys.exit(f"Бронь не создана: {booking.code} {booking.data}")
    protocol.add(f"{label}. Клиент бронирует машину {car['plate']} ({car['brand']} {car['model']}, "
                 f"{car['price_per_minute']} ₸/мин): `POST /api/v1/bookings` -> **{booking.code}**, "
                 f"бронь `{short(booking.data['id'])}…`.", "")
    finish = call("PATCH", f"/api/v1/bookings/{booking.data['id']}", {"status": "finished"}, token=token)
    return booking.data, finish


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_PROTOCOL)
    args = parser.parse_args()
    LOG_DIR.mkdir(exist_ok=True)
    for old in LOG_DIR.glob("*.log"):
        old.unlink()

    env = {"TASK_MAX_ATTEMPTS": "3", "TASK_RETRY_BACKOFF_SECONDS": "2", "PAYMENT_PROVIDER_TIMEOUT": "5"}
    api = Process("api", ["manage.py", "runserver", "8010", "--noreload"], env)
    worker = Process("worker", ["manage.py", "run_worker", "--interval", "0.5"], env)
    provider = Process("provider", ["payment_provider_stub.py", "--latency", str(PROVIDER_LATENCY)])
    processes = [api, worker, provider]
    p = Protocol()
    try:
        provider.start()
        api.start()
        wait_until_up(PROVIDER + "/health", "Эмулятор провайдера")
        wait_until_up(API + "/health", "API")
        worker.start()
        time.sleep(1.5)

        customer, operator, admin = login(*CUSTOMER), login(*OPERATOR), login(*ADMIN)
        own = call("GET", "/api/v1/auth/me", token=customer).data
        active = call("GET", "/api/v1/bookings?status=active", token=customer).data["items"]
        for booking in active:  # хвост прошлого прогона: отменяем, чтобы клиент мог бронировать
            call("PATCH", f"/api/v1/bookings/{booking['id']}", {"status": "cancelled"}, token=admin)

        p.add(
            "# Протокол проверки фонового списания (ПР6)", "",
            f"Дата прогона: {datetime.now():%Y-%m-%d %H:%M:%S}. Протокол сформирован скриптом "
            "`booking-service/async_check.py` на запущенных процессах: API (`runserver 8010`), обработчик "
            "очереди (`run_worker`) и эмулятор платёжного провайдера (`payment_provider_stub.py`, порт 8030). "
            "Все ответы, времена и строки логов ниже — из этого прогона.", "",
            "Параметры: попыток на задачу — 3, пауза перед повтором — 2 с, затем 4 с (удваивается), "
            f"тайм-аут вызова провайдера — 5 с, эмулятор отвечает на списание за {PROVIDER_LATENCY:g} с "
            "(так ведёт себя настоящий банк-эквайер).", "",
            f"Клиент: `{CUSTOMER[0]}` (роль `{own['role']}`), статус очереди смотрит оператор `{OPERATOR[0]}`.", "",
        )

        # --- путь 1 ---------------------------------------------------------
        p.add("## 1. Штатный путь: провайдер работает", "")
        marks = {proc.name: proc.mark() for proc in processes}
        booking, finish = finish_trip(customer, p, "1.1")
        started = time.perf_counter() - finish.elapsed
        payment = finish.data["payment"]
        p.add(f"1.2. Клиент завершает поездку: `PATCH /api/v1/bookings/{short(booking['id'])}…` "
              f"`{{\"status\": \"finished\"}}` -> **{finish.code}** за **{finish.elapsed * 1000:.0f} мс**. "
              f"Поездка закрыта и машина свободна сразу, а списание {finish.data['total_cost']} ₸ только "
              f"поставлено в очередь — платёж `{payment['status']}`, задача `{payment['task']['status']}`:", "")
        p.json({k: finish.data[k] for k in ("id", "status", "total_cost", "finished_at")} | {"payment": payment})
        rows, final = poll_payment(payment["id"], customer, lambda pay, t: pay["status"] != "pending",
                                   30, started)
        p.add("1.3. Клиент опрашивает `GET /api/v1/payments/{id}` (адрес из ответа) — как менялся статус "
              "(время от отправки запроса на завершение):", "")
        p.add(*timeline_table(rows))
        p.add(f"Итог: платёж **`{final['status']}`**, операция провайдера `{final['provider_payment_id']}`, "
              f"задача выполнена с попытки {final['task']['attempts']}.", "")
        time.sleep(1.5)  # чек — следующая задача в очереди
        tasks = call("GET", "/api/v1/tasks?limit=2", token=operator).data["items"]
        p.add("1.4. Оператор смотрит очередь: `GET /api/v1/tasks?limit=2` (две последние задачи — списание и "
              "чек, который ставится только после успешного списания):", "")
        p.json([{k: t[k] for k in ("task", "status", "attempts", "max_attempts", "finished_at", "return_value")}
                for t in tasks])
        p.add(f"Для сравнения: если бы списание шло прямо в запросе (как было до ПР6), ответ на завершение "
              f"поездки ждал бы банк — не меньше {PROVIDER_LATENCY:g} с вместо {finish.elapsed * 1000:.0f} мс.", "")
        p.add("1.5. Лог обработчика (`run_worker`) за этот путь:", "")
        p.code("\n".join(worker.since(marks["worker"])), "text")
        p.add("Лог эмулятора провайдера:", "")
        p.code("\n".join(provider.since(marks["provider"])), "text")

        # --- путь 2 ---------------------------------------------------------
        p.add("## 2. Ошибочный путь: провайдер выключен", "")
        provider.stop()
        p.add("2.1. Процесс эмулятора провайдера принудительно остановлен (`terminate`) — на порту 8030 "
              "никто не слушает, любое подключение получает отказ. (На Windows отказ приходит не сразу, а примерно "
              "через 2 с — система сама повторяет попытку соединения; это видно по длительности попыток в логе.)", "")
        marks = {proc.name: proc.mark() for proc in processes}
        booking, finish = finish_trip(customer, p, "2.2")
        started = time.perf_counter() - finish.elapsed
        payment = finish.data["payment"]
        p.add(f"2.3. Завершение поездки: `PATCH /api/v1/bookings/{short(booking['id'])}…` -> **{finish.code}** "
              f"за **{finish.elapsed * 1000:.0f} мс** — отказ провайдера на ответ клиенту не влияет, "
              f"платёж `{payment['status']}`.", "")

        health = []
        rows, final = [], None
        deadline = time.perf_counter() + 60
        last = None
        while time.perf_counter() < deadline:
            reply = call("GET", f"/api/v1/payments/{payment['id']}", token=customer)
            pay, t = reply.data, reply.data["task"]
            state = (pay["status"], t["status"], t["attempts"], t["last_error"])
            if state != last:
                rows.append((time.perf_counter() - started, pay, t, reply.elapsed))
                last = state
            if len(health) < 3 and t["attempts"] >= len(health) + 1:
                h = call("GET", "/api/v1/cars?status=free")
                health.append((time.perf_counter() - started, h.code, h.elapsed, t["attempts"]))
            if pay["status"] != "pending":
                final = pay
                break
            time.sleep(0.2)
        if final is None:
            sys.exit("Платёж не перешёл в failed за 60 с")
        p.add("2.4. Статус платежа по времени — задача повторяется с паузой 2 с, потом 4 с; пока повторы "
              "идут, платёж остаётся `pending` (клиент видит «оплата обрабатывается», а не ложный сбой):", "")
        p.add(*timeline_table(rows))
        p.add("2.5. API во время сбоя отвечает как обычно (каталог машин запрашивался между попытками):", "")
        p.add("| t, с | Запрос | Код | Время ответа, мс | Попыток списания к этому моменту |", "|---|---|---|---|---|")
        for t, code, elapsed, attempts in health:
            p.add(f"| {t:.1f} | `GET /api/v1/cars?status=free` | {code} | {elapsed * 1000:.0f} | {attempts} |")
        p.add("")
        p.add("2.6. Итоговое состояние платежа — `failed` с причиной; задача `FAILED` после 3 попыток:", "")
        p.json({k: final[k] for k in ("id", "status", "amount", "failure_reason", "paid_at")} | {"task": final["task"]})
        task = call("GET", f"/api/v1/tasks/{payment['task_id']}", token=operator).data
        p.add("Оператор видит причину каждой попытки (`GET /api/v1/tasks/{id}`, без стектрейсов — они только "
              "в базе и админке):", "")
        p.json({k: task[k] for k in ("task", "status", "attempts", "max_attempts", "errors")})
        p.add("2.7. Лог обработчика за ошибочный путь — постановка, три попытки с причинами, окончательный "
              "сбой и перевод платежа в `failed`:", "")
        p.code("\n".join(worker.since(marks["worker"])), "text")
        alive = worker.proc.poll() is None and api.proc.poll() is None
        p.add(f"Процессы API и обработчика после сбоя: {'работают' if alive else 'УПАЛИ'} — "
              "сбой зависимости не роняет приложение.", "")

        # --- восстановление -------------------------------------------------
        p.add("## 3. Восстановление: провайдер снова доступен, клиент повторяет оплату", "")
        provider.start("--fail-first", "1")
        wait_until_up(PROVIDER + "/health", "Эмулятор провайдера")
        marks = {proc.name: proc.mark() for proc in processes}
        p.add("3.1. Эмулятор провайдера запущен заново с ключом `--fail-first 1`: первое списание он "
              "отклонит ответом 503 (перегрузка), следующие проведёт.", "")
        retry = call("POST", f"/api/v1/bookings/{booking['id']}/payments", token=customer)
        started = time.perf_counter() - retry.elapsed
        p.add(f"3.2. Клиент повторяет оплату: `POST /api/v1/bookings/{short(booking['id'])}…/payments` -> "
              f"**{retry.code} Accepted** за {retry.elapsed * 1000:.0f} мс, "
              f"`Location: {retry.headers.get('Location')}`:", "")
        p.json({k: retry.data[k] for k in ("id", "status", "amount", "task_id")} | {"task": retry.data["task"]})
        again = call("POST", f"/api/v1/bookings/{booking['id']}/payments", token=customer)
        p.add(f"Повторный запрос, пока списание идёт, отклоняется: **{again.code}** `{again.data['error']['code']}` "
              "— двух параллельных списаний за одну поездку не бывает.", "")
        rows, final = poll_payment(retry.data["id"], customer, lambda pay, t: pay["status"] != "pending",
                                   30, started)
        p.add("3.3. Статус нового платежа по времени: попытка 1 получила 503, попытка 2 через 2 с прошла:", "")
        p.add(*timeline_table(rows))
        payments = call("GET", f"/api/v1/bookings/{booking['id']}/payments", token=customer).data["items"]
        p.add("3.4. История платежей поездки — неуспешный платёж сохранён, успешный один:", "")
        p.add("| Платёж | Статус | Сумма | Причина сбоя / операция провайдера |", "|---|---|---|---|")
        for pay in payments:
            p.add(f"| `{short(pay['id'])}…` | `{pay['status']}` | {pay['amount']} | "
                  f"{pay['failure_reason'] or pay['provider_payment_id']} |")
        p.add("")
        time.sleep(1.5)
        p.add("3.5. Лог обработчика (текст письма с чеком, который консольный почтовый бэкенд печатает в тот же "
              "поток, здесь опущен — он показан в п. 1.5):", "")
        p.code("\n".join(line for line in worker.since(marks["worker"]) if line.startswith("[")), "text")
        p.add("Лог эмулятора провайдера:", "")
        p.code("\n".join(provider.since(marks["provider"])), "text")
    finally:
        for proc in processes:
            proc.stop()

    p.add("## Вывод", "",
          "* Штатный путь: ответ на завершение поездки не ждёт банк; списание выполняется отдельно от "
          "запроса, его статус и результат видны через `GET /api/v1/payments/{id}` и `GET /api/v1/tasks/{id}`.",
          "* Ошибочный путь: при выключенном провайдере задача повторяется ограниченное число раз с растущей "
          "паузой, причина каждой попытки записана в лог и в задачу; после исчерпания попыток платёж "
          "переходит в `failed` с понятной причиной, API и обработчик продолжают работать.",
          "* Восстановление: клиент повторяет оплату (202 Accepted), временный 503 провайдера переживается "
          "повтором, двойного списания нет.", "")
    args.out.write_text("\n".join(p.lines), encoding="utf-8")
    print(f"Протокол записан: {args.out}")
    print(f"Полные логи процессов: {LOG_DIR}")


if __name__ == "__main__":
    main()
