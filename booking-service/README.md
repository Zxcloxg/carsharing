# booking-service (Django + DRF)

Ядро бизнес-домена каршеринга: пользователи, верификация ВУ, автопарк,
брони/поездки, тарифы, платежи. Публичный REST для мобильного клиента +
админка для операторов.

## Запуск
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
```
При старте в логе: `INFO config.db: Подключение к базе данных установлено: PostgreSQL 17.6, ...`.
Без переменных окружения (в том числе `JWT_SECRET` не короче 32 символов)
сервис не стартует.

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
| GET | /health | Проверка живости сервиса | все |
| POST | /api/v1/auth/register | Регистрация клиента (201; 409, если email занят) | все |
| POST | /api/v1/auth/login | Вход: access-токен (401 при неверных данных) | все |
| GET | /api/v1/auth/me | Текущий пользователь | вход |
| GET | /api/v1/users | Учётные записи: `page`/`limit`, фильтр `role` | admin |
| GET, PATCH | /api/v1/users/{id} | Учётная запись; смена роли и блокировка | admin |
| GET | /api/v1/cars | Список машин: пагинация `page`/`limit`, фильтры `status`, `brand`, `max_price_per_minute` | все |
| POST | /api/v1/cars | Добавить машину (201 + `Location`) | admin |
| GET | /api/v1/cars/{id} | Получить машину | все |
| PUT | /api/v1/cars/{id} | Полностью заменить данные машины | admin |
| PATCH | /api/v1/cars/{id} | Частично обновить (например, статус) | staff |
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
| PATCH | /api/v1/bookings/{id} | Завершить (с оплатой) или отменить бронь (транзакция) | вход (своя) |
| GET | /api/v1/bookings/{id}/payments | Платежи по аренде | вход (свои) |
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
| `fleet/services.py` | Бизнес-логика и транзакции: уникальность номера, допуск к аренде, бронирование, оплата, пагинация |
| `fleet/repositories/` | Слой доступа к данным, по модулю на сущность: `cars`, `car_models`, `customers`, `driver_licenses`, `bookings`, `payments`, `car_status_history` — единственное место с ORM-вызовами |
| `fleet/models.py` | 10 моделей с ограничениями и индексами (см. `docs/database.md`) |
| `fleet/admin.py` | Админка оператора: клиенты и ВУ, автопарк с опциями, аренды и платежи, инциденты |
| `fleet/migrations/` | `0001` — вся схема, `0002` — индекс `cars_status_created_idx` |
| `fleet/management/commands/` | `seed_demo`, `demo_transaction` (демонстрация отката) |
| `fleet/errors.py` | Доменные исключения с HTTP-кодом и машиночитаемым `code` |

## Тесты
```bash
python manage.py test fleet accounts  # 108 тестов на отдельной базе test_<DB_NAME>
python manage.py demo_transaction     # откат транзакций бронирования и завершения аренды при сбое
```

Проверка доступа на запущенном сервисе (после `seed_demo` и `./serve.cmd`):
`./auth_check.cmd` — 35 запросов от имени анонима, клиентов, оператора и
администратора; протокол сохраняется в [`../docs/auth-check.md`](../docs/auth-check.md).
