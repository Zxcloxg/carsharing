# Протокол проверки безопасности API (ПР5) — после исправлений

Дата проверки: 2026-11-11 03:19 (UTC). Сервис: booking-service, `http://127.0.0.1:8010`.
Запросы выполнены скриптом [`booking-service/security_check.py`](../booking-service/security_check.py) к запущенному сервису с реальной базой PostgreSQL (демо-данные `python manage.py seed_demo`). Чек-лист с выводами и записью «до/после» — в [`security.md`](security.md).

## 1. Ограничение частоты запросов (API4:2023, API2:2023)

Лимит — 10 попыток входа и 10 регистраций в минуту с одного IP. Злоумышленник перебирает пароль администратора и в каждом запросе подставляет новый заголовок `X-Forwarded-For`, выдавая себя за другой IP. Все запросы на самом деле идут с одного адреса 127.0.0.1, поэтому после 10 попыток сервис обязан отвечать 429.

| № | Проверка | От имени | Запрос | Ожидается | Получено | Код ошибки | Итог |
|---|---|---|---|---|---|---|---|
| 1.1 | Попытки 1–10: неверный пароль, каждый раз новый X-Forwarded-For | злоумышленник | `POST /api/v1/auth/login` | 401 ×10 | **401 ×10** | `invalid_credentials` | ✅ |
| 1.2 | Попытка 11: снова подменён X-Forwarded-For | злоумышленник | `POST /api/v1/auth/login` | 429 | **429** | `throttled` | ✅ |
| 1.3 | Попытка 12: снова подменён X-Forwarded-For | злоумышленник | `POST /api/v1/auth/login` | 429 | **429** | `throttled` | ✅ |
| 1.4 | Регистрация: 10 запросов подряд (пустое тело) | аноним | `POST /api/v1/auth/register` | 400 ×10 | **400 ×10** | `validation_error` | ✅ |
| 1.5 | Регистрация: запрос 11 | аноним | `POST /api/v1/auth/register` | 429 | **429** | `throttled` | ✅ |

Ответ — «попытка 12»:

```http
HTTP/1.1 429
Content-Type: application/json
Retry-After: 56
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: same-origin
Cross-Origin-Opener-Policy: same-origin

{
  "error": {
    "code": "throttled",
    "message": "Request was throttled. Expected available in 56 seconds.",
    "details": []
  }
}
```

Ответ — «Регистрация: запрос 11»:

```http
HTTP/1.1 429
Content-Type: application/json
Retry-After: 60
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: same-origin
Cross-Origin-Opener-Policy: same-origin

{
  "error": {
    "code": "throttled",
    "message": "Request was throttled. Expected available in 60 seconds.",
    "details": []
  }
}
```

## 2. Инъекции (API8:2023, OWASP Injection)

Значения из запроса попадают в SQL только параметрами ORM Django, поэтому SQL-фрагмент — просто строка: по ней ищется марка, она не исполняется. Ответ — штатный код, не 500.

| № | Проверка | От имени | Запрос | Ожидается | Получено | Код ошибки | Итог |
|---|---|---|---|---|---|---|---|
| 2.1 | SQL-фрагмент в фильтре `brand` | аноним | `GET /api/v1/cars?brand=' OR '1'='1` | 200, найдено 0 | **200, найдено 0** | — | ✅ |
| 2.2 | SQL-фрагмент в фильтре `brand` | аноним | `GET /api/v1/cars?brand=Kia'; DROP TABLE cars; --` | 200, найдено 0 | **200, найдено 0** | — | ✅ |
| 2.3 | SQL-фрагмент в фильтре `brand` | аноним | `GET /api/v1/cars?brand=%' UNION SELECT password_hash FROM users --` | 200, найдено 0 | **200, найдено 0** | — | ✅ |
| 2.4 | SQL-фрагмент в числовом фильтре | аноним | `GET /api/v1/cars?max_price_per_minute=1 OR 1=1` | 400 | **400** | `validation_error` | ✅ |
| 2.5 | SQL-фрагмент вместо id в пути | аноним | `GET /api/v1/cars/' OR 1=1 --` | 404 | **404** | `car_not_found` | ✅ |
| 2.6 | SQL-фрагмент в email при входе | аноним | `POST /api/v1/auth/login` | 400 | **400** | `validation_error` | ✅ |
| 2.7 | SQL-фрагмент в пароле при входе | аноним | `POST /api/v1/auth/login` | 401 | **401** | `invalid_credentials` | ✅ |
| 2.8 | SQL-фрагмент в текстовом поле сохраняется как текст | администратор | `POST /api/v1/cars` | 201 | **201** | — | ✅ |
| 2.9 | Таблица cars цела: машин стало на одну больше | аноним | `GET /api/v1/cars?limit=1` | 200, машин 6 | **200, машин 6** | — | ✅ |

Ответ — «SQL-фрагмент в текстовом поле сохраняется как текст»:

```http
HTTP/1.1 201
Content-Type: application/json
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: same-origin
Cross-Origin-Opener-Policy: same-origin

{
  "plate": "4873SQL",
  "brand": "Robert'); DROP TABLE cars;--",
  "model": "X",
  "year": 2024,
  "color": null,
  "price_per_minute": 50.0,
  "fuel_level": 1.0,
  "status": "free",
  "id": "94ce104d-06c4-46d7-86af-d7bc3ff12d40",
  "created_at": "2026-11-11T03:20:37.116558+00:00",
  "updated_at": "2026-11-11T03:20:37.116558+00:00"
}
```

## 3. Лишние данные в ответах (API3:2023 — Excessive Data Exposure)

Тело ответа обходится рекурсивно; ни на какой глубине не должно быть ключей, похожих на пароль, хэш, соль или секрет.

| № | Проверка | От имени | Запрос | Ожидается | Получено | Код ошибки | Итог |
|---|---|---|---|---|---|---|---|
| 3.1 | Вход: в ответе токен и учётная запись | администратор | `POST /api/v1/auth/login` | 200, без пароля и хэша | **200; поля: access_token, expires_in, token_type, user** | — | ✅ |
| 3.2 | Текущий пользователь | клиент Айгерим | `GET /api/v1/auth/me` | 200, без пароля и хэша | **200; поля: created_at, customer_id, email, id, is_active, last_login_at, role** | — | ✅ |
| 3.3 | Список всех учётных записей | администратор | `GET /api/v1/users?limit=100` | 200, без пароля и хэша | **200; поля: items[].created_at, items[].customer_id, items[].email, items[].id, items[].is_active, items[].last_login_at, items[].role, limit, page, pages, total** | — | ✅ |
| 3.4 | Свой профиль клиента | клиент Айгерим | `GET /api/v1/customers/963f6126-fe67-4496-ae84-13df3aa0346f` | 200, без пароля и хэша | **200; поля: birth_date, created_at, email, full_name, has_valid_license, id, phone, status** | — | ✅ |
| 3.5 | Публичный каталог машин | аноним | `GET /api/v1/cars?limit=100` | 200, без пароля и хэша | **200; поля: items[].brand, items[].color, items[].created_at, items[].fuel_level, items[].id, items[].model, items[].plate, items[].price_per_minute, items[].status, items[].updated_at, items[].year, limit, page, pages, total** | — | ✅ |

Ответ — «Текущий пользователь»:

```http
HTTP/1.1 200
Content-Type: application/json
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: same-origin
Cross-Origin-Opener-Policy: same-origin

{
  "id": "28e585b4-3d2b-471c-b2ba-8ef46cd59efc",
  "email": "aigerim@example.kz",
  "role": "customer",
  "customer_id": "963f6126-fe67-4496-ae84-13df3aa0346f",
  "is_active": true,
  "created_at": "2026-11-11T02:47:49.004639+00:00",
  "last_login_at": "2026-11-11T03:20:35.584458+00:00"
}
```

## 4. Права на уровне полей (API3:2023 — BOPLA, API5:2023 — BFLA)

По матрице ролей (docs/auth.md) оператор меняет у машины только статус и уровень топлива; тариф, госномер, марку и год меняет администратор. Роль при регистрации не выбирается, клиент не может сам снять с себя блокировку.

| № | Проверка | От имени | Запрос | Ожидается | Получено | Код ошибки | Итог |
|---|---|---|---|---|---|---|---|
| 4.1 | Оператор переводит машину в сервис (разрешено) | оператор | `PATCH /api/v1/cars/0875e204-b9ad-4c57-9d1f-8d4aba1ebbf0` | 200 | **200** | — | ✅ |
| 4.2 | Оператор обновляет уровень топлива (разрешено) | оператор | `PATCH /api/v1/cars/0875e204-b9ad-4c57-9d1f-8d4aba1ebbf0` | 200 | **200** | — | ✅ |
| 4.3 | Оператор снижает тариф до 1 ₸/мин | оператор | `PATCH /api/v1/cars/0875e204-b9ad-4c57-9d1f-8d4aba1ebbf0` | 403 | **403** | `permission_denied` | ✅ |
| 4.4 | Оператор меняет госномер | оператор | `PATCH /api/v1/cars/0875e204-b9ad-4c57-9d1f-8d4aba1ebbf0` | 403 | **403** | `permission_denied` | ✅ |
| 4.5 | Тариф машины после попыток оператора | аноним | `GET /api/v1/cars/0875e204-b9ad-4c57-9d1f-8d4aba1ebbf0` | 200, тариф 70 | **200, тариф 70.0** | — | ✅ |
| 4.6 | Администратор меняет тариф (разрешено) | администратор | `PATCH /api/v1/cars/0875e204-b9ad-4c57-9d1f-8d4aba1ebbf0` | 200 | **200** | — | ✅ |
| 4.7 | Клиент меняет статус своего профиля | клиент Айгерим | `PATCH /api/v1/customers/963f6126-fe67-4496-ae84-13df3aa0346f` | 403 | **403** | `permission_denied` | ✅ |

Ответ — «Оператор снижает тариф до 1 ₸/мин»:

```http
HTTP/1.1 403
Content-Type: application/json
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: same-origin
Cross-Origin-Opener-Policy: same-origin

{
  "error": {
    "code": "permission_denied",
    "message": "Role operator cannot change: price_per_minute",
    "details": [
      {
        "field": "price_per_minute",
        "message": "Allowed for role operator: fuel_level, status"
      }
    ]
  }
}
```

## 5. Коды 401/403 на всех защищённых маршрутах (API2:2023, API5:2023)

Перебираются все защищённые маршруты API. Без токена — 401 (`not_authenticated`); клиенту на маршруты персонала и оператору на маршруты администратора — 403 (`permission_denied`). В пути — несуществующий id: права проверяются раньше, чем поиск записи, поэтому по коду ответа нельзя узнать, есть ли запись.

| № | Проверка | От имени | Запрос | Ожидается | Получено | Код ошибки | Итог |
|---|---|---|---|---|---|---|---|
| 5.1 | нужен вход | аноним | `GET /api/v1/auth/me` | 401 | **401** | `not_authenticated` | ✅ |
| 5.2 | только администратор | аноним | `GET /api/v1/users` | 401 | **401** | `not_authenticated` | ✅ |
| 5.3 | только администратор | аноним | `GET /api/v1/users/00000000-0000-4000-8000-000000000000` | 401 | **401** | `not_authenticated` | ✅ |
| 5.4 | только администратор | аноним | `PATCH /api/v1/users/00000000-0000-4000-8000-000000000000` | 401 | **401** | `not_authenticated` | ✅ |
| 5.5 | только администратор | аноним | `POST /api/v1/cars` | 401 | **401** | `not_authenticated` | ✅ |
| 5.6 | только администратор | аноним | `PUT /api/v1/cars/00000000-0000-4000-8000-000000000000` | 401 | **401** | `not_authenticated` | ✅ |
| 5.7 | только персонал | аноним | `PATCH /api/v1/cars/00000000-0000-4000-8000-000000000000` | 401 | **401** | `not_authenticated` | ✅ |
| 5.8 | только администратор | аноним | `DELETE /api/v1/cars/00000000-0000-4000-8000-000000000000` | 401 | **401** | `not_authenticated` | ✅ |
| 5.9 | только персонал | аноним | `GET /api/v1/cars/00000000-0000-4000-8000-000000000000/status-history` | 401 | **401** | `not_authenticated` | ✅ |
| 5.10 | только персонал | аноним | `GET /api/v1/customers` | 401 | **401** | `not_authenticated` | ✅ |
| 5.11 | нужен вход | аноним | `POST /api/v1/customers` | 401 | **401** | `not_authenticated` | ✅ |
| 5.12 | нужен вход | аноним | `GET /api/v1/customers/00000000-0000-4000-8000-000000000000` | 401 | **401** | `not_authenticated` | ✅ |
| 5.13 | только персонал | аноним | `PATCH /api/v1/customers/00000000-0000-4000-8000-000000000000` | 401 | **401** | `not_authenticated` | ✅ |
| 5.14 | нужен вход | аноним | `GET /api/v1/customers/00000000-0000-4000-8000-000000000000/driver-licenses` | 401 | **401** | `not_authenticated` | ✅ |
| 5.15 | нужен вход | аноним | `POST /api/v1/customers/00000000-0000-4000-8000-000000000000/driver-licenses` | 401 | **401** | `not_authenticated` | ✅ |
| 5.16 | нужен вход | аноним | `GET /api/v1/driver-licenses/00000000-0000-4000-8000-000000000000` | 401 | **401** | `not_authenticated` | ✅ |
| 5.17 | только персонал | аноним | `PATCH /api/v1/driver-licenses/00000000-0000-4000-8000-000000000000` | 401 | **401** | `not_authenticated` | ✅ |
| 5.18 | нужен вход | аноним | `GET /api/v1/bookings` | 401 | **401** | `not_authenticated` | ✅ |
| 5.19 | нужен вход | аноним | `POST /api/v1/bookings` | 401 | **401** | `not_authenticated` | ✅ |
| 5.20 | нужен вход | аноним | `GET /api/v1/bookings/00000000-0000-4000-8000-000000000000` | 401 | **401** | `not_authenticated` | ✅ |
| 5.21 | нужен вход | аноним | `PATCH /api/v1/bookings/00000000-0000-4000-8000-000000000000` | 401 | **401** | `not_authenticated` | ✅ |
| 5.22 | нужен вход | аноним | `GET /api/v1/bookings/00000000-0000-4000-8000-000000000000/payments` | 401 | **401** | `not_authenticated` | ✅ |
| 5.23 | только администратор | клиент Айгерим | `GET /api/v1/users` | 403 | **403** | `permission_denied` | ✅ |
| 5.24 | только администратор | клиент Айгерим | `GET /api/v1/users/00000000-0000-4000-8000-000000000000` | 403 | **403** | `permission_denied` | ✅ |
| 5.25 | только администратор | клиент Айгерим | `PATCH /api/v1/users/00000000-0000-4000-8000-000000000000` | 403 | **403** | `permission_denied` | ✅ |
| 5.26 | только администратор | клиент Айгерим | `POST /api/v1/cars` | 403 | **403** | `permission_denied` | ✅ |
| 5.27 | только администратор | клиент Айгерим | `PUT /api/v1/cars/00000000-0000-4000-8000-000000000000` | 403 | **403** | `permission_denied` | ✅ |
| 5.28 | только персонал | клиент Айгерим | `PATCH /api/v1/cars/00000000-0000-4000-8000-000000000000` | 403 | **403** | `permission_denied` | ✅ |
| 5.29 | только администратор | клиент Айгерим | `DELETE /api/v1/cars/00000000-0000-4000-8000-000000000000` | 403 | **403** | `permission_denied` | ✅ |
| 5.30 | только персонал | клиент Айгерим | `GET /api/v1/cars/00000000-0000-4000-8000-000000000000/status-history` | 403 | **403** | `permission_denied` | ✅ |
| 5.31 | только персонал | клиент Айгерим | `GET /api/v1/customers` | 403 | **403** | `permission_denied` | ✅ |
| 5.32 | только персонал | клиент Айгерим | `PATCH /api/v1/customers/00000000-0000-4000-8000-000000000000` | 403 | **403** | `permission_denied` | ✅ |
| 5.33 | только персонал | клиент Айгерим | `PATCH /api/v1/driver-licenses/00000000-0000-4000-8000-000000000000` | 403 | **403** | `permission_denied` | ✅ |
| 5.34 | только администратор | оператор | `GET /api/v1/users` | 403 | **403** | `permission_denied` | ✅ |
| 5.35 | только администратор | оператор | `GET /api/v1/users/00000000-0000-4000-8000-000000000000` | 403 | **403** | `permission_denied` | ✅ |
| 5.36 | только администратор | оператор | `PATCH /api/v1/users/00000000-0000-4000-8000-000000000000` | 403 | **403** | `permission_denied` | ✅ |
| 5.37 | только администратор | оператор | `POST /api/v1/cars` | 403 | **403** | `permission_denied` | ✅ |
| 5.38 | только администратор | оператор | `PUT /api/v1/cars/00000000-0000-4000-8000-000000000000` | 403 | **403** | `permission_denied` | ✅ |
| 5.39 | только администратор | оператор | `DELETE /api/v1/cars/00000000-0000-4000-8000-000000000000` | 403 | **403** | `permission_denied` | ✅ |
| 5.40 | Вход с неверным паролем, в заголовке — токен клиента | клиент Айгерим | `POST /api/v1/auth/login` | 401 | **401** | `invalid_credentials` | ✅ |

Ответ — «Вход с неверным паролем, в заголовке — токен клиента»:

```http
HTTP/1.1 401
Content-Type: application/json
WWW-Authenticate: Bearer realm="api"
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: same-origin
Cross-Origin-Opener-Policy: same-origin

{
  "error": {
    "code": "invalid_credentials",
    "message": "Invalid email or password",
    "details": []
  }
}
```

## 6. Конфигурация сервиса (API8:2023 — Security Misconfiguration)

Режим отладки выключен: на неизвестный адрес сервис не показывает отладочную страницу со списком маршрутов. Ответы API несут защитные заголовки, сбои не раскрывают стектрейс, размер страницы ограничен.

| № | Проверка | От имени | Запрос | Ожидается | Получено | Код ошибки | Итог |
|---|---|---|---|---|---|---|---|
| 6.1 | Неизвестный адрес вне API | аноним | `GET /no-such-page` | 404 без подробностей | **404, без подробностей** | — | ✅ |
| 6.2 | Защитные заголовки ответа | аноним | `GET /api/v1/cars?limit=1` | nosniff, DENY | **200, nosniff, DENY** | — | ✅ |
| 6.3 | Битый JSON в теле | аноним | `POST /api/v1/auth/login` | 400 без стектрейса | **400, без стектрейса** | `parse_error` | ✅ |
| 6.4 | Слишком большая страница списка (limit=1000) | аноним | `GET /api/v1/cars?limit=1000` | 400 | **400** | `validation_error` | ✅ |

Ответ — «Неизвестный адрес вне API»:

```http
HTTP/1.1 404
Content-Type: text/html; charset=utf-8
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: same-origin
Cross-Origin-Opener-Policy: same-origin

(видимый текст HTML-страницы)
Not Found Not Found The requested resource was not found on this server.
```

Ответ — «Защитные заголовки ответа»:

```http
HTTP/1.1 200
Content-Type: application/json
X-Content-Type-Options: nosniff
X-Frame-Options: DENY
Referrer-Policy: same-origin
Cross-Origin-Opener-Policy: same-origin

{
  "items": [
    {
      "plate": "123ABC02",
      "brand": "Kia",
      "model": "Rio",
      "year": 2022,
      "color": "white",
      "price_per_minute": 65.0,
      "fuel_level": 0.8,
      "status": "free",
      "id": "0d09b7c0-d31d-4208-816b-161bc206e997",
      "created_at": "2026-11-11T02:47:47.120623+00:00",
      "updated_at": "2026-11-11T02:47:47.120623+00:00"
    }
  ],
  "page": 1,
  "limit": 1,
  "total": 5,
  "pages": 5
}
```

## Итог

Выполнено проверок: 70, совпало с ожидаемым: 70, расхождений: 0.
