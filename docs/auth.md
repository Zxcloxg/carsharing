# Аутентификация, авторизация и единый формат ошибок (ПР4)

Код: приложение [`booking-service/accounts`](../booking-service/accounts),
обработчик ошибок [`config/exceptions.py`](../booking-service/config/exceptions.py)
и [`config/middleware.py`](../booking-service/config/middleware.py).
Протокол ручной проверки: [`auth-check.md`](auth-check.md).

## 1. Модель пользователя

Таблица `users` (миграция [`accounts/migrations/0001_users.py`](../booking-service/accounts/migrations/0001_users.py)):

| Колонка | Тип | Ограничения | Назначение |
|---|---|---|---|
| `id` | uuid | PK | Идентификатор, попадает в токен (`sub`) |
| `email` | varchar(254) | UNIQUE по `LOWER(email)` | Логин; хранится в нижнем регистре |
| `password_hash` | varchar(255) | NOT NULL, CHECK `^[a-z0-9_]+\$.+\$.+` | Хэш пароля Argon2id |
| `role` | varchar(10) | CHECK in (`customer`, `operator`, `admin`), по умолчанию `customer` | Роль |
| `customer_id` | uuid | FK → `customers.id`, UNIQUE, NULL, ON DELETE SET NULL | Профиль арендатора (1:1); у персонала пустой |
| `is_active` | boolean | по умолчанию `true` | `false` — вход запрещён, уже выданные токены не принимаются |
| `created_at` | timestamptz | NOT NULL | Дата регистрации |
| `last_login_at` | timestamptz | NULL | Последний успешный вход |

Почему отдельная таблица, а не встроенная `auth_user` Django: `auth_user`
остаётся для входа сотрудников в админку `/admin/` (сессии, CSRF), а `users` —
учётные записи публичного API с ровно теми полями, которые нужны API, и
связью с доменной сущностью «клиент».

### Хранение паролей

- Пароль хэшируется **Argon2id** (`argon2-cffi`, первый в `PASSWORD_HASHERS`):
  алгоритм-победитель Password Hashing Competition, требует много памяти и
  поэтому плохо перебирается на GPU. Соль случайная для каждого пароля —
  одинаковые пароли дают разные хэши.
- В базе лежит строка вида `argon2$argon2id$v=19$m=102400,t=2,p=8$<соль>$<хэш>`.
  Открытый пароль нигде не сохраняется, не логируется и не возвращается в
  ответах API.
- CHECK `users_password_is_hashed` — страховка на уровне базы: строка без
  формата `<алгоритм>$…$…` (то есть пароль «как есть») не запишется даже в
  обход API.
- При смене параметров хэширования старый хэш прозрачно пересчитывается при
  следующем входе (`check_password(..., setter=...)`).

## 2. Регистрация — `POST /api/v1/auth/register`

```json
{"email": "user@example.kz", "password": "Tiger#Moon2026"}
```

| Проверка | Ответ |
|---|---|
| email корректного формата, ≤ 254 символов; приводится к нижнему регистру | 400 `validation_error` |
| пароль 8–128 символов (верхняя граница — защита от DoS дорогим хэшированием) | 400 |
| пароль не похож на email, не из списка распространённых, не только цифры (валидаторы Django) | 400 |
| поле `role` в запросе запрещено — роль при регистрации всегда `customer` | 400 |
| email уже занят (без учёта регистра) | 409 `email_taken` |
| успех | 201 + `Location: /api/v1/users/{id}`; пароль и хэш в ответе отсутствуют |

Уникальность проверяется базой (уникальный индекс по `LOWER(email)`), а не
предварительным `SELECT`: так нет гонки двух одновременных регистраций.

## 3. Вход — `POST /api/v1/auth/login`

```json
{"email": "user@example.kz", "password": "Tiger#Moon2026"}
```

Ответ 200:

```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "token_type": "Bearer",
  "expires_in": 3600,
  "user": {"id": "...", "email": "user@example.kz", "role": "customer", "customer_id": null, ...}
}
```

- Неверный пароль и несуществующий email дают **одинаковый** ответ
  401 `invalid_credentials`; для несуществующего email пароль всё равно
  хэшируется, чтобы и по времени ответа нельзя было узнать, зарегистрирован
  ли адрес.
- Заблокированная учётная запись после верного пароля — 403 `account_disabled`.
- Не больше 10 попыток входа (и 10 регистраций) в минуту с одного IP —
  дальше 429 `throttled` (защита от перебора паролей).

### Почему JWT, а не серверная сессия

| Критерий | JWT (выбран) | Серверная сессия |
|---|---|---|
| Клиент | Мобильное приложение: токен в заголовке `Authorization`, cookie не нужны | Ориентирована на браузер и cookie |
| CSRF | Не нужна защита: браузер не подставляет заголовок сам | Нужен CSRF-токен на каждый изменяющий запрос |
| Масштабирование | Проверка — только подпись и срок, общего хранилища сессий нет | Нужна общая таблица/Redis сессий для всех экземпляров |
| Микросервисы | Тот же токен может проверить другой сервис (API Gateway, telemetry-service) по секрету | Каждый сервис ходил бы в хранилище сессий booking-service |
| Отзыв доступа | Минус: токен живёт до `exp` | Сессию можно удалить мгновенно |

Минус JWT закрыт так: срок жизни короткий (60 минут, `JWT_ACCESS_TTL_MINUTES`),
а при каждом запросе пользователь заново читается из базы — блокировка
(`is_active = false`) и смена роли действуют сразу, не дожидаясь истечения
токена.

Параметры токена: подпись HMAC-SHA256 секретом `JWT_SECRET` (только из
переменных окружения, не короче 32 символов, иначе сервис не стартует);
полезная нагрузка `sub` (id), `role`, `iss = booking-service`, `iat`, `exp`.
При проверке алгоритм задан жёстко (`algorithms=["HS256"]`) — токен с
`alg: none` или подписанный другим ключом отклоняется.

## 4. Защита маршрутов

Проверка выполняется в два этапа, и они разделены в коде:

```
запрос ─► JWTAuthentication ─► @require_auth ─► @require_role(...) ─► ensure_owner_or_staff ─► бизнес-логика
          (кто это?)             (есть ли токен?)  (разрешено ли роли?)   (своя ли это запись?)
          битый токен → 401      нет токена → 401   роль не та → 403        чужая запись → 403
```

1. **Аутентификация** — класс [`JWTAuthentication`](../booking-service/accounts/authentication.py),
   подключённый глобально в `REST_FRAMEWORK.DEFAULT_AUTHENTICATION_CLASSES`,
   то есть работает как middleware для всех маршрутов API. Разбирает заголовок
   `Authorization: Bearer <token>`, проверяет подпись, срок, издателя и
   активность пользователя. Нет заголовка — запрос анонимный; заголовок есть,
   но токен плохой — сразу 401 (`invalid_token`, `token_expired`,
   `account_disabled`) с заголовком `WWW-Authenticate: Bearer realm="api"`.
2. **Авторизация** — декораторы из [`accounts/permissions.py`](../booking-service/accounts/permissions.py)
   на методах представлений:
   - `@require_auth` — нужен любой валидный токен, иначе 401 `not_authenticated`;
   - `@require_role(Role.ADMIN, ...)` — сначала та же проверка (поэтому аноним
     всегда получает 401, а не 403), затем роль: не та — 403 `permission_denied`
     с указанием разрешённых ролей в `details`.
3. **Права владельца** — `ensure_owner_or_staff(user, customer_id)`: клиент
   работает только со своими профилем, ВУ и бронями; персонал — с любыми.

Роль берётся из базы, а не из токена: если администратор понизил роль
пользователя, старый токен с `role: admin` уже не даст прав администратора.

## 5. Роли и права

| Операция | Аноним | customer | operator | admin |
|---|---|---|---|---|
| `GET /cars`, `GET /cars/{id}` — каталог | ✅ | ✅ | ✅ | ✅ |
| `POST /cars`, `PUT /cars/{id}` | 401 | 403 | 403 | ✅ |
| `PATCH /cars/{id}` (статус, топливо) | 401 | 403 | ✅ | ✅ |
| `DELETE /cars/{id}` | 401 | 403 | 403 | ✅ |
| `GET /cars/{id}/status-history` | 401 | 403 | ✅ | ✅ |
| `GET /customers` — все клиенты | 401 | 403 | ✅ | ✅ |
| `POST /customers` | 401 | свой профиль (один) | за клиента | за клиента |
| `GET /customers/{id}` | 401 | только свой (иначе 403) | ✅ | ✅ |
| `PATCH /customers/{id}` — блокировка | 401 | 403 | ✅ | ✅ |
| `GET, POST /customers/{id}/driver-licenses` | 401 | только свои | ✅ | ✅ |
| `GET /driver-licenses/{id}` | 401 | только своя | ✅ | ✅ |
| `PATCH /driver-licenses/{id}` — решение по ВУ | 401 | 403 (даже по своей заявке) | ✅ | ✅ |
| `GET /bookings` | 401 | только свои (чужой `customer_id` → 403) | все | все |
| `POST /bookings` | 401 | только на себя (`customer_phone` не нужен) | за клиента (`customer_phone` обязателен) | за клиента |
| `GET, PATCH /bookings/{id}`, `GET /bookings/{id}/payments` | 401 | только свои | ✅ | ✅ |
| `GET /auth/me` | 401 | ✅ | ✅ | ✅ |
| `GET /users`, `GET, PATCH /users/{id}` — роли и блокировка | 401 | 403 | 403 | ✅ (кроме себя: 409) |

Первый администратор создаётся командой (пароль спрашивается интерактивно):

```bash
python manage.py create_user admin@carsharing.kz --role admin
```

Дальше администратор назначает роли через `PATCH /api/v1/users/{id}`
(`{"role": "operator"}`) и блокирует учётные записи (`{"is_active": false}`).
Изменить собственную роль или заблокировать себя нельзя — чтобы система не
осталась без администратора.

## 6. Единый формат ошибок

Любая ошибка API — валидация, аутентификация, авторизация, «не найдено»,
конфликт, внутренняя ошибка — имеет одну форму:

```json
{
  "error": {
    "code": "validation_error",
    "message": "Validation failed",
    "details": [
      {"field": "password", "message": "This password is too common."}
    ]
  }
}
```

- `code` — стабильный машиночитаемый код: по нему клиент решает, что делать
  (`token_expired` → войти заново, `validation_error` → подсветить поля);
- `message` — текст для человека;
- `details` — список уточнений, для валидации — по элементу на поле; пустой,
  если уточнять нечего.

| HTTP | `code` | Когда |
|---|---|---|
| 400 | `validation_error` | Тело или параметры запроса не прошли схему |
| 400 | `parse_error` | Тело — не корректный JSON |
| 401 | `not_authenticated` | Токена нет, а маршрут защищён |
| 401 | `invalid_token` | Токен испорчен, подписан чужим ключом, неверная схема заголовка, пользователь удалён |
| 401 | `token_expired` | Истёк срок действия токена |
| 401 | `account_disabled` | Учётная запись заблокирована после выдачи токена |
| 401 | `invalid_credentials` | Неверный email или пароль при входе |
| 403 | `permission_denied` | Роль не позволяет действие или запись чужая |
| 403 | `account_disabled` | Вход в заблокированную учётную запись |
| 403 | `customer_profile_required`, `customer_cannot_rent` | Нет профиля арендатора; ВУ не подтверждено или клиент заблокирован |
| 404 | `not_found`, `car_not_found`, `booking_not_found`, … | Неизвестный URL или записи нет |
| 405 | `method_not_allowed` | Метод не поддерживается для пути |
| 409 | `email_taken`, `plate_already_used`, `car_not_available`, … | Конфликт с текущим состоянием данных |
| 415 | `unsupported_media_type` | Тело не `application/json` |
| 429 | `throttled` | Слишком много попыток входа или регистрации |
| 500 | `internal_error` | Непредвиденный сбой |

Как это обеспечено:

- [`config/exceptions.py:api_exception_handler`](../booking-service/config/exceptions.py)
  — глобальный обработчик DRF (`REST_FRAMEWORK.EXCEPTION_HANDLER`): приводит к
  формату доменные исключения (`DomainError` с полями `status_code`, `code`,
  `message`, `details`), исключения DRF (400/401/403/404/405/415/429) и
  **любое необработанное исключение** — оно пишется в лог сервера со
  стектрейсом, транзакция откатывается, а клиент получает 500
  `internal_error` без внутренних подробностей.
- [`config/middleware.py:ApiErrorMiddleware`](../booking-service/config/middleware.py)
  — для путей `/api/` вне маршрутов DRF: неизвестный URL даёт JSON 404 вместо
  HTML-страницы Django, исключение вне APIView — JSON 500 вместо HTML-стектрейса.

## 7. Проверка

```bash
python manage.py test fleet accounts   # 108 автотестов, из них 62 — ПР4 (accounts/tests.py)
python manage.py seed_demo             # демо-пользователи всех ролей
./serve.cmd                            # в одном окне
./auth_check.cmd                       # в другом: 35 проверок, протокол -> docs/auth-check.md
```

Демо-учётные записи (только для локальной проверки):

| Email | Пароль | Роль |
|---|---|---|
| admin@carsharing.kz | Steppe#Falcon2026 | admin |
| operator@carsharing.kz | Orbit#Signal2026 | operator |
| aigerim@example.kz | Tulip#Morning2026 | customer (профиль клиента +77011234567) |
| daniyar@example.kz | River#Stone2026 | customer (профиль клиента +77027654321) |
