# booking-service (Django + DRF)

Ядро бизнес-домена каршеринга: пользователи, верификация ВУ, автопарк,
брони/поездки, тарифы, платежи. Публичный REST для мобильного клиента +
админка для операторов.

## Запуск в Docker (ПР7)
Из корня репозитория: `cp .env.example .env` (заполнить секреты), затем
`docker compose up -d --build --wait`. Образ собирается по [`Dockerfile`](Dockerfile);
роль контейнера (`api`, `worker`, `migrate`, `provider`) выбирает
[`docker/entrypoint.sh`](docker/entrypoint.sh), а настройки gunicorn лежат в
[`gunicorn.conf.py`](gunicorn.conf.py). Подробно — в
[`../docs/deployment.md`](../docs/deployment.md).

## Запуск без Docker
Нужен PostgreSQL (локальный или в Docker:
`docker run -d --name carsharing-pg -e POSTGRES_DB=carsharing -e POSTGRES_USER=carsharing -e POSTGRES_PASSWORD=carsharing -p 5432:5432 postgres:17`).

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env               # DB_HOST, DB_PORT, DB_NAME, DB_USER, DB_PASSWORD, JWT_SECRET
python manage.py migrate           # вся схема с нуля, включая таблицу users
python manage.py seed_demo         # демо-данные и демо-пользователи всех ролей (необязательно)
python manage.py create_user admin@carsharing.kz --role admin   # или свой первый администратор
python manage.py runserver 8010
python manage.py run_worker        # отдельное окно: обработчик фоновых задач (ПР6), Windows: worker.cmd
python payment_provider_stub.py    # отдельное окно: эмулятор платёжного провайдера, Windows: payment_provider.cmd
```
Без обработчика API работает, но платежи за поездки остаются в статусе `pending`: списание
ждёт в очереди и выполнится, как только обработчик запустится. Подробно — в
[`../docs/background-tasks.md`](../docs/background-tasks.md).

При старте в логе: `INFO config.db: Подключение к базе данных установлено: PostgreSQL 17.6, ...`.
Без переменных окружения (в том числе `JWT_SECRET` не короче 32 символов и
`DJANGO_SECRET_KEY` не короче 50) сервис не стартует. Режим отладки по умолчанию выключен;
для локальной отладки (подробные страницы ошибок, стили админки при `runserver`) —
`DJANGO_DEBUG=true` в `.env`. Все переменные — в [`.env.example`](.env.example).

Схема данных, индексы, миграции и транзакции: [`../docs/database.md`](../docs/database.md).
Аутентификация, роли и формат ошибок: [`../docs/auth.md`](../docs/auth.md).

## Аутентификация (ПР4)
Вход по email и паролю, дальше каждый запрос несёт заголовок
`Authorization: Bearer <access_token>` (JWT, HS256, 60 минут). Пароли хранятся
только в виде хэша Argon2id. Роли: `customer` — клиент, `operator` — оператор,
`admin` — администратор. Почему JWT, а не сессия, — в корневом
[`README`](../README.md#аутентификация-и-авторизация-пр4) и в [`docs/auth.md`](../docs/auth.md).

```bash
curl -X POST http://127.0.0.1:8010/api/v1/auth/login -H "Content-Type: application/json" \
     -d '{"email":"admin@carsharing.kz","password":"Steppe#Falcon2026"}'
curl -X DELETE http://127.0.0.1:8010/api/v1/cars/<id> -H "Authorization: Bearer <access_token>"
```

Демо-пользователи (`seed_demo`, только для локальной проверки): `admin@carsharing.kz` /
`Steppe#Falcon2026`, `operator@carsharing.kz` / `Orbit#Signal2026`,
клиенты `aigerim@example.kz` / `Tulip#Morning2026` и `daniyar@example.kz` /
`River#Stone2026`.

## Эндпоинты
Доступ: «все» — без токена; «вход» — любой пользователь с токеном (клиент —
только к своим записям); `staff` — `operator` и `admin`.

| Метод | Путь | Назначение | Доступ |
|---|---|---|---|
| GET | /health | Готовность: процесс и база данных (503, если база недоступна), состояние очереди (ПР7) | все |
| GET | /health/live | Живость процесса без проверки зависимостей (ПР7) | все |
| GET | /metrics | Метрики в формате Prometheus (ПР7); с `METRICS_TOKEN` — по Bearer-токену | все / по токену |
| POST | /api/v1/auth/register | Регистрация клиента (201; 409, если email занят) | все |
| POST | /api/v1/auth/login | Вход: access-токен (401 при неверных данных) | все |
| GET | /api/v1/auth/me | Текущий пользователь | вход |
| GET | /api/v1/users | Учётные записи: `page`/`limit`, фильтр `role` | admin |
| GET, PATCH | /api/v1/users/{id} | Учётная запись; смена роли и блокировка | admin |
| GET | /api/v1/cars | Список машин: пагинация `page`/`limit`, фильтры `status`, `brand`, `max_price_per_minute` | все |
| POST | /api/v1/cars | Добавить машину (201 + `Location`) | admin |
| GET | /api/v1/cars/{id} | Получить машину | все |
| PUT | /api/v1/cars/{id} | Полностью заменить данные машины | admin |
| PATCH | /api/v1/cars/{id} | Частично обновить: оператор — только `status` и `fuel_level`, администратор — любые поля | staff |
| DELETE | /api/v1/cars/{id} | Убрать машину из автопарка (204; 409, если есть брони) | admin |
| GET | /api/v1/cars/{id}/status-history | Журнал смены статусов машины | staff |
| GET | /api/v1/customers | Список клиентов: `page`/`limit`, фильтр `status` | staff |
| POST | /api/v1/customers | Профиль арендатора (18+, телефон и email уникальны); клиент заводит свой, он привязывается к учётной записи | вход |
| GET | /api/v1/customers/{id} | Получить клиента (`has_valid_license` — допущен ли к аренде) | вход (свой) |
| PATCH | /api/v1/customers/{id} | Заблокировать / разблокировать клиента | staff |
| GET | /api/v1/customers/{id}/driver-licenses | Заявки клиента на проверку ВУ | вход (свои) |
| POST | /api/v1/customers/{id}/driver-licenses | Подать ВУ на проверку | вход (свои) |
| GET | /api/v1/driver-licenses/{id} | Получить заявку | вход (своя) |
| PATCH | /api/v1/driver-licenses/{id} | Решение оператора: `approved` / `rejected` | staff |
| GET | /api/v1/bookings | Список броней: `page`/`limit`, фильтры `car_id`, `customer_id`, `status` | вход (клиент — свои) |
| POST | /api/v1/bookings | Забронировать машину (транзакция; только клиент с подтверждённым ВУ) | вход (клиент — на себя) |
| GET | /api/v1/bookings/{id} | Получить бронь | вход (своя) |
| PATCH | /api/v1/bookings/{id} | Завершить или отменить бронь (транзакция); при завершении — платёж `pending`, списание в фоне (ПР6) | вход (своя) |
| GET | /api/v1/bookings/{id}/payments | Платежи по аренде | вход (свои) |
| POST | /api/v1/bookings/{id}/payments | Повторить оплату после сбоя: 202 Accepted + `Location` (ПР6) | вход (своя) |
| GET | /api/v1/payments/{id} | Статус платежа и его фоновой задачи (ПР6) | вход (свой) |
| GET | /api/v1/tasks | Очередь фоновых задач: фильтры `status`, `task` (ПР6) | staff |
| GET | /api/v1/tasks/{id} | Задача: попытки, причины сбоев, результат (ПР6) | staff |
| —  | /admin/ | Админка Django (для операторов, вход по `auth_user`) | — |

Все ошибки — в едином формате `{"error": {"code", "message", "details"}}`
(таблица кодов — в [`../docs/auth.md`](../docs/auth.md#6-единый-формат-ошибок)).

Формальный контракт: [`openapi.yaml`](openapi.yaml). Описание сущности, URI и
формата ошибок: [`../docs/api.md`](../docs/api.md). Примеры запросов и ответов:
[`../docs/examples.http`](../docs/examples.http).

## Структура приложений
Слои разделены: маршрут -> бизнес-логика -> хранение.

### `accounts` — учётные записи и доступ (ПР4)

| Файл | Ответственность |
|---|---|
| `accounts/models.py` | Модель `User` (таблица `users`), роли `customer` / `operator` / `admin` |
| `accounts/migrations/0001_users.py` | Миграция таблицы пользователей |
| `accounts/authentication.py` | Проверка JWT для каждого запроса (глобально, как middleware) |
| `accounts/permissions.py` | Декораторы `require_auth` (401) и `require_role` (403), проверка владельца записи |
| `accounts/tokens.py` | Выпуск и проверка access-токенов |
| `accounts/services.py` | Регистрация, вход, управление ролями, привязка профиля клиента |
| `accounts/repository.py` | Доступ к таблице `users` |
| `accounts/throttling.py` | Ограничение частоты входа и регистрации по адресу клиента (ПР5) |
| `accounts/serializers.py` | Валидация email и пароля |
| `accounts/management/commands/create_user.py` | Создание первого администратора |
| `config/exceptions.py` | Единый формат ошибок и глобальный обработчик (включая 500) |
| `config/middleware.py` | JSON-ошибки под `/api/` вне маршрутов DRF (неизвестный URL, сбой) |

### `fleet` — автопарк, клиенты, брони

| Файл | Ответственность |
|---|---|
| `fleet/urls.py` | Пути ресурса |
| `fleet/views.py` | Маршруты: разбор запроса и коды ответов |
| `fleet/serializers.py` | Валидация входных данных (схемы/DTO) |
| `fleet/domain.py` | Правила предметной области — чистые функции без базы: тарификация, возраст, допуск к аренде, ВУ, пагинация (ПР5) |
| `fleet/services.py` | Бизнес-логика и транзакции: уникальность номера, бронирование, постановка и результат списания, повторная оплата; правила берёт из `domain.py` |
| `fleet/tasks.py` | Фоновые задачи `charge_payment` и `send_receipt` (`@task` из `django.tasks`), перевод платежа в `failed` по сигналу (ПР6) |
| `fleet/payment_gateway.py` | HTTP-клиент платёжного провайдера: тайм-аут, ключ идемпотентности, временные и окончательные ошибки (ПР6) |
| `fleet/repositories/` | Слой доступа к данным, по модулю на сущность: `cars`, `car_models`, `customers`, `driver_licenses`, `bookings`, `payments`, `car_status_history` — единственное место с ORM-вызовами |
| `fleet/models.py` | 10 моделей с ограничениями и индексами (см. `docs/database.md`) |
| `fleet/admin.py` | Админка оператора: клиенты и ВУ, автопарк с опциями, аренды и платежи, инциденты |
| `fleet/migrations/` | `0001` — вся схема, `0002` — индекс `cars_status_created_idx`, `0003` — поля платежа для фонового списания |
| `fleet/management/commands/` | `seed_demo`, `demo_transaction` (демонстрация отката) |
| `fleet/errors.py` | Доменные исключения с HTTP-кодом и машиночитаемым `code` |

### `jobs` — очередь фоновых задач (ПР6)

| Файл | Ответственность |
|---|---|
| `jobs/models.py` | Таблица `background_tasks`: задача, аргументы, статус, попытки, `run_after`, ошибки, результат |
| `jobs/backends.py` | Бэкенд `django.tasks`: постановка задачи `INSERT`-ом в транзакции вызывающего, чтение статуса |
| `jobs/worker.py` | Обработчик: выбор задачи (`FOR UPDATE SKIP LOCKED`), выполнение, повтор с паузой, окончательный сбой, перехват зависших задач, логи |
| `jobs/retry.py` | Политика повторов: экспоненциальная пауза, временные и окончательные ошибки |
| `jobs/services.py`, `views.py`, `urls.py` | Статус задач для API (`/api/v1/tasks`) |
| `jobs/admin.py` | Очередь в админке, действие «Запустить заново» |
| `jobs/management/commands/run_worker.py` | `python manage.py run_worker [--once] [--interval 1]` |
| `payment_provider_stub.py` | Эмулятор платёжного провайдера (порт 8030; `--latency`, `--fail-first`, `--decline-over`) |
| `async_check.py` | Проверка штатного и ошибочного пути на живых процессах -> `../docs/async-scenario.md` |

### `tests/` — автотесты (ПР5)

| Путь | Ответственность |
|---|---|
| `tests/unit/` | Модульные тесты: `domain.py`, схемы валидации, JWT, права, формат ошибок, хэширование, лимиты |
| `tests/api/` | API- и интеграционные тесты эндпоинтов, ограничений базы и команд управления |
| `tests/security/` | Чек-лист OWASP API: 401/403 на всех маршрутах, лимиты, инъекции, лишние поля, права на поля, конфигурация |
| `tests/helpers.py` | Общие данные и фабрики, базовый класс `ApiTestCase` (откат транзакции и чистый кэш) |
| `tests/runner.py` | Запуск тестов с облегчёнными параметрами Argon2id |
| `.coveragerc`, `coverage.cmd` | Настройки и запуск отчёта о покрытии |

## Тестирование (ПР5)

Тесты лежат в [`tests/`](tests): `unit/` — модульные тесты бизнес-правил, валидации,
токенов и прав (без базы и сети), `api/` — HTTP-эндпоинты через тестовый клиент,
`security/` — проверки по OWASP API Security. Подробно: [`../docs/testing.md`](../docs/testing.md).

**1. Подготовка (один раз).** Нужен запущенный PostgreSQL и заполненный `.env`, как для
запуска сервиса. Пользователю из `DB_USER` нужно право создавать базы: тесты работают в
отдельной базе `test_<DB_NAME>`, которую Django создаёт и удаляет сам.

```bash
pip install -r requirements.txt -r requirements-dev.txt   # + coverage и PyYAML
```

**2. Запуск тестов** — одна команда, 352 теста (после ПР7), меньше минуты. Линтер — `ruff check .`:

```bash
python manage.py test tests                 # Windows: test.cmd
python manage.py test tests.unit            # только модульные (109), база не нужна
python manage.py test tests.security        # только безопасность (45)
python manage.py test tests --shuffle       # случайный порядок: тесты не зависят друг от друга
```

**3. Покрытие:**

```bash
python -m coverage run manage.py test tests # Windows: coverage.cmd (делает все три шага)
python -m coverage report                   # таблица в консоли
python -m coverage html                     # отчёт по строкам: htmlcov/index.html
```

Покрытие — **97,8 %** (строки 98,4 %, ветвления 93,7 %; в ПР5 — 97,9 %). Слабее всего покрыты действия
админки Django (88,6 %) и методы `__str__` моделей — это интерфейс сотрудников, он проверялся
вручную. Остальные пропуски — защитные ветки на случай гонок и неизвестных ошибок базы, до
которых нельзя дойти через API. Разбор по модулям — в
[`../docs/testing.md`](../docs/testing.md#покрытие).

**4. Проверки на запущенном сервисе** (после `python manage.py seed_demo` и `./serve.cmd`):

```bash
./auth_check.cmd       # ПР4: 35 сценариев доступа -> ../docs/auth-check.md
./security_check.cmd   # ПР5: проверки безопасности (с ПР6 — 76) -> ../docs/security-check.md
./async_check.cmd      # ПР6: штатный путь и сбой провайдера -> ../docs/async-scenario.md (сам запускает процессы)
python manage.py demo_transaction   # откат транзакций бронирования и завершения аренды при сбое
```

Обе проверки расходуют лимит попыток входа (10 в минуту с IP), поэтому перед повторным
запуском сервис нужно перезапустить или подождать минуту.

Чек-лист безопасности и исправленные уязвимости: [`../docs/security.md`](../docs/security.md).
