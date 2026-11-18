# Протокол финальной проверки развёртывания (ПР7)

Дата: 2026-11-18 15:58:42. Протокол сформирован скриптом `deploy_check.py`: система поднята с нуля только через `docker compose`, все запросы ниже — к контейнерам, все выводы — из этого прогона.

| Окружение | Значение |
|---|---|
| Docker | 29.8.1 (Docker Desktop, 8 CPU, 899 МиБ памяти у виртуальной машины Docker) |
| Docker Compose | 5.5.1 |
| Код | рабочая копия поверх коммита `74299ae` (изменения ПР7 ещё не закоммичены) |

## 1. Запуск с нуля одной командой

`docker compose down -v --remove-orphans` — удалены контейнеры и том базы `pgdata`: база пустая.

`docker compose build` — 28 с (слой зависимостей берётся из кэша, пока не менялся requirements.txt). Образы:

```text
carsharing/booking-service:latest  287MB
carsharing/telemetry-service:latest  187MB
```

`docker compose up -d --wait` — вся система поднялась за 54 с:

```text
SERVICE            STATUS                      PORTS
api                Up 9 seconds (healthy)      0.0.0.0:8010->8010/tcp, [::]:8010->8010/tcp
db                 Up 44 seconds (healthy)     5432/tcp
migrate            Exited (0) 12 seconds ago   
payment-provider   Up 48 seconds (healthy)     8010/tcp
telemetry          Up 46 seconds (healthy)     0.0.0.0:8020->8020/tcp, [::]:8020->8020/tcp
worker             Up 10 seconds               8010/tcp
```

✅ **Все сервисы с healthcheck в состоянии healthy** — db: healthy, api: healthy, payment-provider: healthy, telemetry: healthy

✅ **Одноразовый сервис migrate завершился успешно** — exited (код 0)

## 2. Миграции применены автоматически

```text
Operations to perform:
Running migrations:
  Applying contenttypes.0001_initial... OK
  Applying auth.0001_initial... OK
  Applying fleet.0001_initial_schema... OK
  Applying fleet.0002_add_cars_status_index... OK
  Applying accounts.0001_users... OK
  Applying admin.0001_initial... OK
  Applying admin.0002_logentry_remove_auto_add... OK
  Applying admin.0003_logentry_add_action_flag_choices... OK
  Applying contenttypes.0002_remove_content_type_name... OK
  Applying auth.0002_alter_permission_name_max_length... OK
  Applying auth.0003_alter_user_email_max_length... OK
  Applying auth.0004_alter_user_username_opts... OK
  Applying auth.0005_alter_user_last_login_null... OK
  Applying auth.0006_require_contenttypes_0002... OK
  Applying auth.0007_alter_validators_add_error_messages... OK
  Applying auth.0008_alter_user_username_max_length... OK
  Applying auth.0009_alter_user_last_name_max_length... OK
  Applying auth.0010_alter_group_name_max_length... OK
  Applying auth.0011_update_proxy_permissions... OK
  Applying auth.0012_alter_user_first_name_max_length... OK
  Applying fleet.0003_payment_background_charge... OK
  Applying jobs.0001_background_tasks... OK
  Applying sessions.0001_initial... OK
…
Добавлено: моделей 5, опций 5, машин 5, клиентов 4, аренд 2, инцидентов 1
Добавлено учётных записей: admin@carsharing.kz (admin), operator@carsharing.kz (operator), aigerim@example.kz (customer), daniyar@example.kz (customer)
```

✅ **Схема применена на пустой базе** — применено миграций: 23, неприменённых: 0, таблиц в схеме public: 22

## 3. Образ и секреты

```text
$ docker compose exec api id
uid=999(app) gid=999(app) groups=999(app)
$ docker compose exec api sh -c "tr '\\0' ' ' < /proc/1/cmdline"
/usr/local/bin/python3.12 /usr/local/bin/gunicorn config.wsgi:application --config gunicorn.conf.py
```

✅ **Процесс API работает не от root** — uid=999(app) gid=999(app) groups=999(app)

✅ **API запущен production-сервером gunicorn, а не runserver**

Переменные окружения, зашитые в образ (`docker image inspect … .Config.Env`):

```text
["PATH=/usr/local/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin","LANG=C.UTF-8","GPG_KEY=7169605F62C751356D054A26A821E680E5FA6305","PYTHON_VERSION=3.12.16","PYTHON_SHA256=b5796915dc45ff745cfb4e990a7e7c93d6f546328a102ce31448d69ebc37575c","PYTHONDONTWRITEBYTECODE=1","PYTHONUNBUFFERED=1","PIP_NO_CACHE_DIR=1","PIP_DISABLE_PIP_VERSION_CHECK=1"]
```

✅ **Секретов из .env нет ни в образе, ни в docker-compose.yml** — проверено значений: 4; найдено в образе: нет, в compose: нет

✅ **.env не отслеживается git** — git ls-files .env booking-service/.env — пусто

## 4. Проверка состояния

```text
GET /health
-> HTTP 200 за 176 мс
{
  "status": "ok",
  "service": "booking-service",
  "framework": "django",
  "time": "2026-11-18T10:00:30.472116+00:00",
  "uptime_s": 23.4,
  "checks": {
    "database": {
      "status": "ok",
      "latency_ms": 2.4
    },
    "queue": {
      "status": "ok",
      "ready": 0,
      "running": 0,
      "failed": 0,
      "oldest_ready_age_s": 0
    }
  }
}
```

✅ **/health: 200, база и очередь в порядке**

✅ **telemetry-service /health: 200** — {"framework":"flask","service":"telemetry-service","status":"ok","time":"2026-11-18T10:00:30.501614+00:00","tracked_cars

## 5. Ключевые сценарии ПР2–ПР6 внутри контейнеров

### ПР4. Регистрация, вход, роли

```text
POST /api/v1/auth/register
{"email": "deploy-66ef87@example.kz", "password": "…"}
-> HTTP 201 за 393 мс
{
  "id": "d71fb42a-4cb5-4777-a3b5-f31582e99097",
  "email": "deploy-66ef87@example.kz",
  "role": "customer",
  "customer_id": null,
  "is_active": true,
  "created_at": "2026-11-18T10:00:32.214218+00:00",
  "last_login_at": null
}
```

✅ **Регистрация 201, вход выдаёт JWT, /auth/me знает пользователя** — роль customer

✅ **Без токена 401, клиенту на маршрут администратора 403** — 401 not_authenticated, 403 permission_denied

### ПР2. CRUD машины (администратор)

```text
POST /api/v1/cars
{"plate": "777DPL02", "brand": "Chevrolet", "model": "Cobalt", "year": 2024, "color": "white", "price_per_minute": 55, "fuel_level": 0.9}
-> HTTP 201 за 249 мс
{
  "id": "0e37c71c-505a-4404-a6bd-54683dc04ea8",
  "plate": "777DPL02",
  "brand": "Chevrolet",
  "model": "Cobalt",
  "status": "free",
  "price_per_minute": 55.0
}
```

✅ **POST 201 + Location, GET 200, PATCH 200, фильтр списка, валидация 400** — Location /api/v1/cars/0e37c71c-505a-4404-a6bd-54683dc04ea8; ошибки валидации: plate, year

✅ **DELETE 204, затем GET 404** — car_not_found

### ПР3–ПР4. Профиль клиента, проверка ВУ оператором, бронь

✅ **Профиль 201, заявка ВУ 201, до проверки бронь 403, оператор подтвердил ВУ** — бронь до проверки: 403 customer_cannot_rent

### ПР6. Фоновое списание: штатный путь

```text
PATCH /api/v1/bookings/e4f11f34…
{"status": "finished"}
-> HTTP 200 за 203 мс
{
  "status": "finished",
  "total_cost": 60.0,
  "payment": {
    "id": "d57725e2-738a-4ee8-8317-fd193f720c1b",
    "booking_id": "e4f11f34-67bd-4d82-8f34-9ea866eed434",
    "amount": 60.0,
    "status": "pending",
    "provider_payment_id": null,
    "failure_reason": null,
    "task_id": "08c030be-fe24-45d9-91b8-64a088fc1b96",
    "created_at": "2026-11-18T10:00:33.872517+00:00",
    "paid_at": null,
    "task": {
      "id": "08c030be-fe24-45d9-91b8-64a088fc1b96",
      "status": "READY",
      "attempts": 0,
      "max_attempts": 3,
      "next_attempt_at": "2026-11-18T10:00:33.923109+00:00",
      "last_error": null,
      "finished_at": null
    }
  }
}
```

✅ **Бронь 201; завершение отвечает сразу с платежом pending; обработчик в контейнере worker провёл оплату** — завершение за 203 мс, итог: paid, операция ch_70c60bfefb654543a8e5

Строки JSON-лога контейнера worker по этой задаче (`docker compose logs worker`, фильтр по task_id):

```json
{"time": "2026-11-18T10:00:34.560+00:00", "level": "INFO", "logger": "jobs", "message": "Задача 08c030be (charge_payment) — попытка 1 из 3, обработчик 7cf351cbffb9:1", "task_id": "08c030be-fe24-45d9-91b8-64a088fc1b96"}
{"time": "2026-11-18T10:00:34.737+00:00", "level": "INFO", "logger": "fleet.payments", "message": "Платёж d57725e2: запрос к провайдеру http://payment-provider:8030/v1/charges, сумма 60.00 ₸, тайм-аут 5 с", "task_id": "08c030be-fe24-45d9-91b8-64a088fc1b96"}
{"time": "2026-11-18T10:00:36.406+00:00", "level": "INFO", "logger": "fleet.payments", "message": "Платёж d57725e2: провайдер подтвердил списание ch_70c60bfefb654543a8e5 за 1.67 с", "task_id": "08c030be-fe24-45d9-91b8-64a088fc1b96"}
{"time": "2026-11-18T10:00:36.440+00:00", "level": "INFO", "logger": "fleet.payments", "message": "Платёж d57725e2 оплачен: 60.00 ₸, операция ch_70c60bfefb654543a8e5", "task_id": "08c030be-fe24-45d9-91b8-64a088fc1b96"}
{"time": "2026-11-18T10:00:36.471+00:00", "level": "INFO", "logger": "jobs", "message": "Задача send_receipt поставлена в очередь: id=4432cf05, аргументы payment_id=d57725e2-738a-4ee8-8317-fd193f720c1b, попыток не больше 3", "task_id": "08c030be-fe24-45d9-91b8-64a088fc1b96"}
{"time": "2026-11-18T10:00:36.486+00:00", "level": "INFO", "logger": "jobs", "message": "Задача 08c030be (charge_payment) выполнена за 1.90 с с попытки 1: {'payment_id': 'd57725e2-738a-4ee8-8317-fd193f720c1b', 'status': 'paid', 'provider_payment_id': 'ch_70c60bfefb654543a8e5'}", "task_id": "08c030be-fe24-45d9-91b8-64a088fc1b96"}
```

### ПР6. Фоновое списание: контейнер провайдера остановлен

`docker compose stop payment-provider`

```text
GET /api/v1/payments/f04310c5…
-> HTTP 200 (последний ответ при опросе)
{
  "status": "failed",
  "failure_reason": "PaymentProviderUnavailable: Provider is unreachable at http://payment-provider:8030/v1/charges: [Errno -2] Name or service not known",
  "task": {
    "id": "f7a1c342-e3a5-4dd9-9471-38f77744ae9a",
    "status": "FAILED",
    "attempts": 3,
    "max_attempts": 3,
    "next_attempt_at": null,
    "last_error": "PaymentProviderUnavailable: Provider is unreachable at http://payment-provider:8030/v1/charges: [Errno -2] Name or service not known",
    "finished_at": "2026-11-18T10:00:57.128420+00:00"
  }
}
```

✅ **Поездка закрыта сразу, после 3 попыток платёж failed с причиной, API отвечает** — завершение за 107 мс; каталог во время сбоя: 200 за 34 мс

WARNING и ERROR в логе worker по этой задаче:

```json
{"time": "2026-11-18T10:00:46.413+00:00", "level": "WARNING", "logger": "jobs", "message": "Задача f7a1c342 (charge_payment) — сбой попытки 1 из 3 через 2.46 с: PaymentProviderUnavailable: Provider is unreachable at http://payment-provider:8030/v1/charges: [Errno -2] Name or service not known. Повтор через 2 с", "task_id": "f7a1c342-e3a5-4dd9-9471-38f77744ae9a"}
{"time": "2026-11-18T10:00:50.809+00:00", "level": "WARNING", "logger": "jobs", "message": "Задача f7a1c342 (charge_payment) — сбой попытки 2 из 3 через 2.32 с: PaymentProviderUnavailable: Provider is unreachable at http://payment-provider:8030/v1/charges: [Errno -2] Name or service not known. Повтор через 4 с", "task_id": "f7a1c342-e3a5-4dd9-9471-38f77744ae9a"}
{"time": "2026-11-18T10:00:57.127+00:00", "level": "ERROR", "logger": "jobs", "message": "Задача f7a1c342 (charge_payment) завершилась сбоем на попытке 3 из 3 (попытки исчерпаны): PaymentProviderUnavailable: Provider is unreachable at http://payment-provider:8030/v1/charges: [Errno -2] Name or service not known", "task_id": "f7a1c342-e3a5-4dd9-9471-38f77744ae9a"}
{"time": "2026-11-18T10:00:57.176+00:00", "level": "ERROR", "logger": "fleet.payments", "message": "Платёж f04310c5 не прошёл: PaymentProviderUnavailable: Provider is unreachable at http://payment-provider:8030/v1/charges: [Errno -2] Name or service not known. Повторить оплату: POST /api/v1/bookings/4bfa2591-4327-449d-a3b6-a520f0019365/payments", "task_id": "f7a1c342-e3a5-4dd9-9471-38f77744ae9a"}
```

✅ **После `docker compose start payment-provider` повторная оплата: 202 Accepted -> paid** — Location /api/v1/payments/3b4ed639-1f75-4c8f-85ae-0ebcf95a30b7

## 6. Наблюдаемость: логи и метрики

Запрос с заголовком `X-Request-ID: deploy-check-probe-1` и его строка в логе контейнера api:

```json
{"time": "2026-11-18T10:01:13.395+00:00", "level": "INFO", "logger": "config.requests", "message": "GET /api/v1/cars -> 200 за 24.8 мс", "request_id": "deploy-check-probe-1", "method": "GET", "path": "/api/v1/cars", "route": "/api/v1/cars", "status": 200, "duration_ms": 24.8}
```

✅ **Ответ несёт X-Request-ID, в JSON-логе — тот же id, маршрут, код и длительность** — duration_ms=24.8

`GET /metrics` с токеном из .env (фрагмент):

```text
process_uptime_seconds 67.9
http_requests_total{method="PATCH",route="/api/v1/bookings/{booking_id}",status="200"} 2
http_requests_total{method="POST",route="/api/v1/bookings",status="201"} 2
http_requests_total{method="POST",route="/api/v1/bookings",status="403"} 1
http_requests_total{method="POST",route="/api/v1/bookings/{booking_id}/payments",status="202"} 1
http_requests_total{method="POST",route="/api/v1/cars",status="201"} 2
http_requests_total{method="POST",route="/api/v1/cars",status="400"} 1
http_requests_total{method="POST",route="/api/v1/cars",status="401"} 1
http_requests_total{method="POST",route="/api/v1/cars",status="403"} 1
http_request_duration_seconds_sum{route="/api/v1/bookings/{booking_id}"} 0.274393
http_request_duration_seconds_count{route="/api/v1/bookings/{booking_id}"} 2
background_tasks{status="READY"} 0
background_tasks{status="RUNNING"} 0
background_tasks{status="FAILED"} 1
background_tasks{status="SUCCESSFUL"} 6
payments{status="pending"} 0
payments{status="paid"} 3
payments{status="failed"} 1
payments{status="refunded"} 0
database_up 1
```

✅ **/metrics: без токена 401, с токеном — счётчики запросов, время ответа, очередь и платежи**

## 7. /health при остановленной базе данных

`docker compose stop db`

```text
GET /health
-> HTTP 503 за 8110 мс
{
  "status": "unavailable",
  "service": "booking-service",
  "framework": "django",
  "time": "2026-11-18T10:01:24.777554+00:00",
  "uptime_s": 77.7,
  "checks": {
    "database": {
      "status": "error",
      "error": "OperationalError: database is unreachable"
    }
  }
}
```

✅ **/health отвечает 503 unavailable, контейнер api помечен unhealthy** — статус контейнера api: unhealthy

Причина — в логе контейнера api (наружу её подробности не отдаются):

```json
{"time": "2026-11-18T10:01:24.776+00:00", "level": "ERROR", "logger": "health", "message": "Проверка здоровья: база данных недоступна: OperationalError: failed to resolve host 'db': [Errno -3] Temporary failure in name resolution", "request_id": "1b90c39317fc4ae3aef8739c409455a5"}
```

✅ **После `docker compose start db` /health снова 200, контейнер api снова healthy — без перезапуска API**

## 8. Данные переживают пересоздание контейнеров

`docker compose down` (без `-v`: том `pgdata` остаётся) и снова `docker compose up -d --wait`.

```text
  No migrations to apply.
Автопарк не пуст — демо-данные автопарка не добавлены
Учётные записи уже есть — демо-пользователи не добавлены
```

✅ **Машина, созданная до пересоздания, на месте; повторный migrate ничего не применяет и не дублирует** — машин Chevrolet до: 1, после: 1

## Итог

Проверок: 22, пройдено: 22.

| № | Проверка | Результат |
|---|---|---|
| 1 | Все сервисы с healthcheck в состоянии healthy | ✅ |
| 2 | Одноразовый сервис migrate завершился успешно | ✅ |
| 3 | Схема применена на пустой базе | ✅ |
| 4 | Процесс API работает не от root | ✅ |
| 5 | API запущен production-сервером gunicorn, а не runserver | ✅ |
| 6 | Секретов из .env нет ни в образе, ни в docker-compose.yml | ✅ |
| 7 | .env не отслеживается git | ✅ |
| 8 | /health: 200, база и очередь в порядке | ✅ |
| 9 | telemetry-service /health: 200 | ✅ |
| 10 | Регистрация 201, вход выдаёт JWT, /auth/me знает пользователя | ✅ |
| 11 | Без токена 401, клиенту на маршрут администратора 403 | ✅ |
| 12 | POST 201 + Location, GET 200, PATCH 200, фильтр списка, валидация 400 | ✅ |
| 13 | DELETE 204, затем GET 404 | ✅ |
| 14 | Профиль 201, заявка ВУ 201, до проверки бронь 403, оператор подтвердил ВУ | ✅ |
| 15 | Бронь 201; завершение отвечает сразу с платежом pending; обработчик в контейнере worker провёл оплату | ✅ |
| 16 | Поездка закрыта сразу, после 3 попыток платёж failed с причиной, API отвечает | ✅ |
| 17 | После `docker compose start payment-provider` повторная оплата: 202 Accepted -> paid | ✅ |
| 18 | Ответ несёт X-Request-ID, в JSON-логе — тот же id, маршрут, код и длительность | ✅ |
| 19 | /metrics: без токена 401, с токеном — счётчики запросов, время ответа, очередь и платежи | ✅ |
| 20 | /health отвечает 503 unavailable, контейнер api помечен unhealthy | ✅ |
| 21 | После `docker compose start db` /health снова 200, контейнер api снова healthy — без перезапуска API | ✅ |
| 22 | Машина, созданная до пересоздания, на месте; повторный migrate ничего не применяет и не дублирует | ✅ |
