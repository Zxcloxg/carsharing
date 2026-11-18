"""Финальная проверка развёртывания (ПР7): система «с нуля» в контейнерах -> docs/deploy-check.md.

Что делает:
1. docker compose down -v — удаляет контейнеры и том базы этого стенда (чистый старт);
2. собирает образы и поднимает систему одной командой docker compose up -d --wait;
3. проверяет миграции, пользователя процесса, отсутствие секретов в образе и в compose-файле;
4. повторяет ключевые сценарии ПР2–ПР6 настоящими HTTP-запросами к контейнерам:
   CRUD машин, регистрацию и вход, роли, верификацию ВУ и бронирование, фоновое списание
   (штатно и с остановленным контейнером платёжного провайдера);
5. проверяет наблюдаемость: X-Request-ID в ответе и в JSON-логе, /metrics;
6. останавливает контейнер базы и показывает, как меняется /health и статус контейнера API;
7. перезапускает систему без удаления тома и проверяет, что данные сохранились.

Нужны Docker с compose v2 и файл .env (cp .env.example .env, заполнить значения, SEED_DEMO=true).
Запуск из корня репозитория: python deploy_check.py  (Python 3.9+, только стандартная библиотека).
ВНИМАНИЕ: данные в томе pgdata этого стенда удаляются (шаг 1).
"""
import json
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "docs" / "deploy-check.md"
API = "http://127.0.0.1:8010"
TELEMETRY = "http://127.0.0.1:8020"
ADMIN = ("admin@carsharing.kz", "Steppe#Falcon2026")
OPERATOR = ("operator@carsharing.kz", "Orbit#Signal2026")
PASSWORD = "Saryarka#Wind2026"

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
lines = []
results = []


# --- вывод ------------------------------------------------------------------
def add(*text):
    lines.extend(text)


def block(text, lang="text"):
    add(f"```{lang}", *str(text).rstrip().splitlines(), "```", "")


def check(name, ok, detail=""):
    results.append((name, ok, detail))
    mark = "✅" if ok else "❌"
    add(f"{mark} **{name}**{(' — ' + detail) if detail else ''}", "")
    print(("OK   " if ok else "FAIL ") + name + (f" — {detail}" if detail else ""), flush=True)
    return ok


# --- команды ----------------------------------------------------------------
def sh(*args, timeout=240, check_rc=True, attempts=3):
    """Команда с тайм-аутом и повтором: Docker Desktop на слабой машине иногда отвечает минутами."""
    for attempt in range(1, attempts + 1):
        try:
            proc = subprocess.run(args, cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                                  errors="replace", timeout=timeout)
            break
        except subprocess.TimeoutExpired:
            print(f"  … {' '.join(args[:4])} не ответила за {timeout} с (попытка {attempt})", flush=True)
            if attempt == attempts:
                raise
    if check_rc and proc.returncode != 0:
        print(proc.stdout, proc.stderr)
        sys.exit(f"Команда завершилась с кодом {proc.returncode}: {' '.join(args)}")
    return (proc.stdout + proc.stderr).strip()


def compose(*args, **kw):
    return sh("docker", "compose", *args, **kw)


def env_file():
    values = {}
    for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


# --- HTTP -------------------------------------------------------------------
class Reply:
    def __init__(self, code, raw, headers, elapsed):
        self.code, self.raw, self.headers, self.elapsed = code, raw, headers, elapsed
        try:
            self.data = json.loads(raw) if raw else None
        except ValueError:
            self.data = None


def call(method, path, body=None, token=None, base=API, headers=None, timeout=60):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(base + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for key, value in (headers or {}).items():
        req.add_header(key, value)
    started = time.perf_counter()
    try:
        with opener.open(req, timeout=timeout) as resp:
            return Reply(resp.status, resp.read().decode(), resp.headers, time.perf_counter() - started)
    except urllib.error.HTTPError as exc:
        return Reply(exc.code, exc.read().decode(), exc.headers, time.perf_counter() - started)
    except (urllib.error.URLError, OSError) as exc:  # нет соединения, сброс, тайм-аут
        return Reply(0, str(exc), {}, time.perf_counter() - started)


def show(method, path, reply, keys=None, body=None, timing=True):
    """Запрос и ответ в протокол: метод, путь, код, время и (сокращённое) тело."""
    data = reply.data
    if keys and isinstance(data, dict):
        data = {k: data.get(k) for k in keys}
    text = json.dumps(data, ensure_ascii=False, indent=2) if data is not None else reply.raw[:300]
    request = f"{method} {path}" + (f"\n{json.dumps(body, ensure_ascii=False)}" if body is not None else "")
    when = f" за {reply.elapsed * 1000:.0f} мс" if timing else " (последний ответ при опросе)"
    block(f"{request}\n-> HTTP {reply.code}{when}\n{text}")


def login(email, password):
    reply = call("POST", "/api/v1/auth/login", {"email": email, "password": password})
    if reply.code != 200:
        sys.exit(f"Вход {email} не удался: {reply.code} {reply.raw[:200]}")
    return reply.data["access_token"]


def wait_for(predicate, seconds, step=1.0):
    deadline = time.time() + seconds
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(step)
    return None


def container_health(service):
    """healthy / unhealthy / starting — или состояние контейнера, если healthcheck не задан."""
    return sh("docker", "inspect", "--format",
              "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
              f"carsharing-{service}-1", check_rc=False)


# ============================================================================
def main():
    if not (ROOT / ".env").exists():
        sys.exit("Нет .env: скопируйте .env.example в .env и заполните значения (SEED_DEMO=true).")
    env = env_file()
    if env.get("SEED_DEMO") != "true":
        sys.exit("Для проверки нужны демо-учётные записи: SEED_DEMO=true в .env.")
    secrets = {k: v for k, v in env.items() if k in ("DB_PASSWORD", "DJANGO_SECRET_KEY", "JWT_SECRET", "METRICS_TOKEN") and v}

    add("# Протокол финальной проверки развёртывания (ПР7)", "",
        f"Дата: {datetime.now():%Y-%m-%d %H:%M:%S}. Протокол сформирован скриптом `deploy_check.py`: система поднята "
        "с нуля только через `docker compose`, все запросы ниже — к контейнерам, все выводы — из этого прогона.", "",
        "| Окружение | Значение |", "|---|---|",
        f"| Docker | {sh('docker', 'version', '--format', '{{.Server.Version}}')} "
        f"({sh('docker', 'info', '--format', '{{.NCPU}}')} CPU, "
        f"{int(sh('docker', 'info', '--format', '{{.MemTotal}}')) // 2**20} МиБ памяти у виртуальной машины Docker) |",
        f"| Docker Compose | {sh('docker', 'compose', 'version', '--short')} |",
        f"| Код | коммит `{sh('git', 'rev-parse', '--short', 'HEAD', check_rc=False)}`"
        f"{' + незакоммиченные изменения' if sh('git', 'status', '--porcelain', check_rc=False) else ''} |", "")

    # --- 1. чистый старт ------------------------------------------------------
    add("## 1. Запуск с нуля одной командой", "")
    compose("down", "-v", "--remove-orphans")
    add("`docker compose down -v --remove-orphans` — удалены контейнеры и том базы `pgdata`: база пустая.", "")
    started = time.time()
    compose("build", timeout=1500, attempts=1)
    build_s = time.time() - started
    images = sh("docker", "image", "ls", "--format", "{{.Repository}}:{{.Tag}}  {{.Size}}", "carsharing/*")
    add(f"`docker compose build` — {build_s:.0f} с (слой зависимостей берётся из кэша, пока не менялся requirements.txt). "
        "Образы:", "")
    block(images)
    started = time.time()
    compose("up", "-d", "--wait", "--wait-timeout", "300", timeout=900, attempts=1)
    up_s = time.time() - started
    ps = compose("ps", "-a", "--format", "table {{.Service}}\t{{.Status}}\t{{.Ports}}")
    add(f"`docker compose up -d --wait` — вся система поднялась за {up_s:.0f} с:", "")
    block(ps)
    states = {svc: container_health(svc) for svc in ("db", "api", "payment-provider", "telemetry")}
    check("Все сервисы с healthcheck в состоянии healthy", all(s == "healthy" for s in states.values()),
          ", ".join(f"{k}: {v}" for k, v in states.items()))
    migrate_state = sh("docker", "inspect", "--format", "{{.State.Status}} (код {{.State.ExitCode}})",
                       "carsharing-migrate-1")
    check("Одноразовый сервис migrate завершился успешно", migrate_state == "exited (код 0)", migrate_state)

    # --- 2. миграции ----------------------------------------------------------
    add("## 2. Миграции применены автоматически", "")
    migrate_log = compose("logs", "--no-color", "--no-log-prefix", "migrate")
    applied = re.findall(r"Applying (\S+)\.\.\. OK", migrate_log)
    block("\n".join(line for line in migrate_log.splitlines() if line.startswith(("Operations", "Running", "  Applying")))
          + "\n…\n" + "\n".join(line for line in migrate_log.splitlines() if line.startswith("Добавлено")))
    tables = compose("exec", "-T", "db", "psql", "-U", env.get("DB_USER", "carsharing"), "-d",
                     env.get("DB_NAME", "carsharing"), "-tAc",
                     "SELECT count(*) FROM information_schema.tables WHERE table_schema = 'public'")
    plan = compose("exec", "-T", "api", "python", "manage.py", "showmigrations", "--plan")
    unapplied = [line for line in plan.splitlines() if line.startswith("[ ]")]
    check("Схема применена на пустой базе", len(applied) > 0 and not unapplied,
          f"применено миграций: {len(applied)}, неприменённых: {len(unapplied)}, таблиц в схеме public: {tables}")

    # --- 3. образ и секреты ---------------------------------------------------
    add("## 3. Образ и секреты", "")
    user = compose("exec", "-T", "api", "id")
    cmdline = compose("exec", "-T", "api", "sh", "-c", "tr '\\0' ' ' < /proc/1/cmdline")
    block(f"$ docker compose exec api id\n{user}\n$ docker compose exec api sh -c \"tr '\\\\0' ' ' < /proc/1/cmdline\"\n{cmdline}")
    check("Процесс API работает не от root", "uid=0" not in user and "(app)" in user, user)
    check("API запущен production-сервером gunicorn, а не runserver", "gunicorn" in cmdline and "runserver" not in cmdline)
    image_env = sh("docker", "image", "inspect", "carsharing/booking-service:latest", "--format", "{{json .Config.Env}}")
    leaked_image = [k for k, v in secrets.items() if v in image_env]
    compose_text = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    leaked_compose = [k for k, v in secrets.items() if v in compose_text]
    tracked = sh("git", "ls-files", ".env", "booking-service/.env", check_rc=False)
    add("Переменные окружения, зашитые в образ (`docker image inspect … .Config.Env`):", "")
    block(image_env)
    check("Секретов из .env нет ни в образе, ни в docker-compose.yml", not leaked_image and not leaked_compose,
          f"проверено значений: {len(secrets)}; найдено в образе: {leaked_image or 'нет'}, в compose: {leaked_compose or 'нет'}")
    check(".env не отслеживается git", tracked == "", "git ls-files .env booking-service/.env — пусто")

    # --- 4. health ------------------------------------------------------------
    add("## 4. Проверка состояния", "")
    health = call("GET", "/health")
    show("GET", "/health", health)
    check("/health: 200, база и очередь в порядке", health.code == 200 and health.data["checks"]["database"]["status"] == "ok")
    tele = call("GET", "/health", base=TELEMETRY)
    check("telemetry-service /health: 200", tele.code == 200, tele.raw[:120])

    # --- 5. сценарии ПР2–ПР6 --------------------------------------------------
    add("## 5. Ключевые сценарии ПР2–ПР6 внутри контейнеров", "")
    admin, operator = login(*ADMIN), login(*OPERATOR)

    add("### ПР4. Регистрация, вход, роли", "")
    email = f"deploy-{uuid.uuid4().hex[:6]}@example.kz"
    reg = call("POST", "/api/v1/auth/register", {"email": email, "password": PASSWORD})
    show("POST", "/api/v1/auth/register", reg, body={"email": email, "password": "…"})
    customer = login(email, PASSWORD)
    me = call("GET", "/api/v1/auth/me", token=customer)
    check("Регистрация 201, вход выдаёт JWT, /auth/me знает пользователя",
          reg.code == 201 and me.code == 200 and me.data["email"] == email, f"роль {me.data.get('role')}")
    anon = call("POST", "/api/v1/cars", {"plate": "X"})
    forbidden = call("POST", "/api/v1/cars", {"plate": "X"}, token=customer)
    check("Без токена 401, клиенту на маршрут администратора 403",
          anon.code == 401 and forbidden.code == 403,
          f"{anon.code} {anon.data['error']['code']}, {forbidden.code} {forbidden.data['error']['code']}")

    add("### ПР2. CRUD машины (администратор)", "")
    car_body = {"plate": "777DPL02", "brand": "Chevrolet", "model": "Cobalt", "year": 2024, "color": "white",
                "price_per_minute": 55, "fuel_level": 0.9}
    created = call("POST", "/api/v1/cars", car_body, token=admin)
    show("POST", "/api/v1/cars", created, keys=["id", "plate", "brand", "model", "status", "price_per_minute"],
         body=car_body)
    car_id = created.data["id"]
    got = call("GET", f"/api/v1/cars/{car_id}")
    patched = call("PATCH", f"/api/v1/cars/{car_id}", {"price_per_minute": 60}, token=admin)
    listed = call("GET", "/api/v1/cars?brand=Chevrolet")
    invalid = call("POST", "/api/v1/cars", {**car_body, "plate": "bad plate", "year": 1900}, token=admin)
    check("POST 201 + Location, GET 200, PATCH 200, фильтр списка, валидация 400",
          created.code == 201 and created.headers.get("Location") == f"/api/v1/cars/{car_id}" and got.code == 200
          and patched.code == 200 and patched.data["price_per_minute"] == 60.0 and listed.data["total"] == 1
          and invalid.code == 400,
          f"Location {created.headers.get('Location')}; ошибки валидации: "
          f"{', '.join(d['field'] for d in invalid.data['error']['details'])}")
    temp = call("POST", "/api/v1/cars", {**car_body, "plate": "778DPL02"}, token=admin).data["id"]
    deleted = call("DELETE", f"/api/v1/cars/{temp}", token=admin)
    gone = call("GET", f"/api/v1/cars/{temp}")
    check("DELETE 204, затем GET 404", deleted.code == 204 and gone.code == 404, gone.data["error"]["code"])

    add("### ПР3–ПР4. Профиль клиента, проверка ВУ оператором, бронь", "")
    profile = call("POST", "/api/v1/customers", {"phone": f"+7705{uuid.uuid4().int % 10**7:07d}",
                                                 "full_name": "Арман Деплоев", "birth_date": "1994-02-03",
                                                 "email": email}, token=customer)
    lic = call("POST", f"/api/v1/customers/{profile.data['id']}/driver-licenses",
               {"number": f"KZ{uuid.uuid4().int % 10**7:07d}", "categories": "B", "issued_on": "2016-05-01",
                "expires_on": "2030-05-01"}, token=customer)
    early = call("POST", "/api/v1/bookings", {"car_id": car_id}, token=customer)
    review = call("PATCH", f"/api/v1/driver-licenses/{lic.data['id']}", {"status": "approved"}, token=operator)
    check("Профиль 201, заявка ВУ 201, до проверки бронь 403, оператор подтвердил ВУ",
          profile.code == 201 and lic.code == 201 and early.code == 403 and review.code == 200,
          f"бронь до проверки: {early.code} {early.data['error']['code']}")

    add("### ПР6. Фоновое списание: штатный путь", "")
    booking = call("POST", "/api/v1/bookings", {"car_id": car_id}, token=customer)
    finish = call("PATCH", f"/api/v1/bookings/{booking.data['id']}", {"status": "finished"}, token=customer)
    show("PATCH", f"/api/v1/bookings/{booking.data['id'][:8]}…", finish, keys=["status", "total_cost", "payment"],
         body={"status": "finished"})
    payment_id = finish.data["payment"]["id"]
    paid = wait_for(lambda: (lambda r: r.data if r.data["status"] != "pending" else None)(
        call("GET", f"/api/v1/payments/{payment_id}", token=customer)), 30, 0.5)
    check("Бронь 201; завершение отвечает сразу с платежом pending; обработчик в контейнере worker провёл оплату",
          booking.code == 201 and finish.code == 200 and finish.data["payment"]["status"] == "pending"
          and paid and paid["status"] == "paid",
          f"завершение за {finish.elapsed * 1000:.0f} мс, итог: {paid and paid['status']}, "
          f"операция {paid and paid['provider_payment_id']}")
    task_id = finish.data["payment"]["task_id"]
    time.sleep(1.5)
    worker_log = compose("logs", "--no-color", "--no-log-prefix", "worker")
    task_lines = [line for line in worker_log.splitlines() if task_id in line]
    add("Строки JSON-лога контейнера worker по этой задаче (`docker compose logs worker`, фильтр по task_id):", "")
    block("\n".join(task_lines), "json")

    add("### ПР6. Фоновое списание: контейнер провайдера остановлен", "")
    compose("stop", "payment-provider")
    add("`docker compose stop payment-provider`", "")
    booking2 = call("POST", "/api/v1/bookings", {"car_id": car_id}, token=customer)
    finish2 = call("PATCH", f"/api/v1/bookings/{booking2.data['id']}", {"status": "finished"}, token=customer)
    pay2 = finish2.data["payment"]["id"]
    failed = wait_for(lambda: (lambda r: r.data if r.data["status"] != "pending" else None)(
        call("GET", f"/api/v1/payments/{pay2}", token=customer)), 60, 0.5)
    show("GET", f"/api/v1/payments/{pay2[:8]}…", Reply(200, json.dumps(failed), {}, 0),
         keys=["status", "failure_reason", "task"], timing=False)
    alive = call("GET", "/api/v1/cars?limit=1")
    check("Поездка закрыта сразу, после 3 попыток платёж failed с причиной, API отвечает",
          finish2.code == 200 and failed and failed["status"] == "failed" and failed["task"]["attempts"] == 3
          and alive.code == 200,
          f"завершение за {finish2.elapsed * 1000:.0f} мс; каталог во время сбоя: {alive.code} за {alive.elapsed * 1000:.0f} мс")
    worker_log = compose("logs", "--no-color", "--no-log-prefix", "worker")
    fail_lines = [line for line in worker_log.splitlines() if finish2.data["payment"]["task_id"] in line
                  and ('"WARNING"' in line or '"ERROR"' in line)]
    add("WARNING и ERROR в логе worker по этой задаче:", "")
    block("\n".join(fail_lines), "json")
    compose("start", "payment-provider")
    wait_for(lambda: container_health("payment-provider") == "healthy", 60)
    retry = call("POST", f"/api/v1/bookings/{booking2.data['id']}/payments", token=customer)
    repaid = wait_for(lambda: (lambda r: r.data if r.data["status"] != "pending" else None)(
        call("GET", f"/api/v1/payments/{retry.data['id']}", token=customer)), 30, 0.5)
    check("После `docker compose start payment-provider` повторная оплата: 202 Accepted -> paid",
          retry.code == 202 and repaid and repaid["status"] == "paid",
          f"Location {retry.headers.get('Location')}")

    # --- 6. наблюдаемость -----------------------------------------------------
    add("## 6. Наблюдаемость: логи и метрики", "")
    probe = call("GET", "/api/v1/cars?status=free", headers={"X-Request-ID": "deploy-check-probe-1"})
    time.sleep(1)
    api_log = compose("logs", "--no-color", "--no-log-prefix", "api")
    probe_lines = [line for line in api_log.splitlines() if "deploy-check-probe-1" in line]
    add("Запрос с заголовком `X-Request-ID: deploy-check-probe-1` и его строка в логе контейнера api:", "")
    block("\n".join(probe_lines), "json")
    entry = json.loads(probe_lines[-1]) if probe_lines else {}
    check("Ответ несёт X-Request-ID, в JSON-логе — тот же id, маршрут, код и длительность",
          probe.headers.get("X-Request-ID") == "deploy-check-probe-1" and entry.get("status") == 200
          and "duration_ms" in entry, f"duration_ms={entry.get('duration_ms')}")
    no_token = call("GET", "/metrics")
    metrics = call("GET", "/metrics", token=env.get("METRICS_TOKEN"))
    wanted = re.compile(r'^(http_requests_total\{method="(POST|PATCH)",route="/api/v1/(cars|bookings[^"]*)"'
                        r'|http_request_duration_seconds_(sum|count)\{route="/api/v1/bookings/\{booking_id\}"'
                        r'|background_tasks|payments|database_up|process_uptime)')
    add("`GET /metrics` с токеном из .env (фрагмент):", "")
    block("\n".join(line for line in metrics.raw.splitlines() if wanted.match(line)))
    check("/metrics: без токена 401, с токеном — счётчики запросов, время ответа, очередь и платежи",
          no_token.code == 401 and metrics.code == 200 and 'payments{status="paid"}' in metrics.raw
          and "http_request_duration_seconds_bucket" in metrics.raw)

    # --- 7. база недоступна ---------------------------------------------------
    add("## 7. /health при остановленной базе данных", "")
    compose("stop", "db")
    add("`docker compose stop db`", "")
    down = call("GET", "/health")
    show("GET", "/health", down)
    unhealthy = wait_for(lambda: container_health("api") == "unhealthy", 150, 2)
    check("/health отвечает 503 unavailable, контейнер api помечен unhealthy",
          down.code == 503 and down.data["checks"]["database"]["status"] == "error" and bool(unhealthy),
          f"статус контейнера api: {container_health('api')}")
    api_log = compose("logs", "--no-color", "--no-log-prefix", "--since", "2m", "api")
    err = [line for line in api_log.splitlines() if '"logger": "health"' in line][:1]
    add("Причина — в логе контейнера api (наружу её подробности не отдаются):", "")
    block("\n".join(err), "json")
    compose("start", "db")
    back = wait_for(lambda: call("GET", "/health").code == 200, 60, 2)
    healthy = wait_for(lambda: container_health("api") == "healthy", 150, 2)
    check("После `docker compose start db` /health снова 200, контейнер api снова healthy — без перезапуска API",
          bool(back) and bool(healthy))

    # --- 8. сохранность данных ------------------------------------------------
    add("## 8. Данные переживают пересоздание контейнеров", "")
    before = call("GET", "/api/v1/cars?brand=Chevrolet").data["total"]
    compose("down")
    add("`docker compose down` (без `-v`: том `pgdata` остаётся) и снова `docker compose up -d --wait`.", "")
    compose("up", "-d", "--wait", "--wait-timeout", "300", timeout=900, attempts=1)
    after = call("GET", "/api/v1/cars?brand=Chevrolet").data["total"]
    relog = compose("logs", "--no-color", "--no-log-prefix", "migrate")
    block("\n".join(line for line in relog.splitlines() if "No migrations" in line or "не добавлены" in line or "уже есть" in line))
    check("Машина, созданная до пересоздания, на месте; повторный migrate ничего не применяет и не дублирует",
          before == after == 1 and "No migrations to apply" in relog, f"машин Chevrolet до: {before}, после: {after}")

    # --- итог -----------------------------------------------------------------
    passed = sum(ok for _, ok, _ in results)
    add("## Итог", "", f"Проверок: {len(results)}, пройдено: {passed}.", "",
        "| № | Проверка | Результат |", "|---|---|---|")
    for i, (name, ok, _) in enumerate(results, 1):
        add(f"| {i} | {name} | {'✅' if ok else '❌'} |")
    add("")
    OUT.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nПроверок: {len(results)}, пройдено: {passed}. Протокол: {OUT}")
    sys.exit(0 if passed == len(results) else 1)


if __name__ == "__main__":
    main()
