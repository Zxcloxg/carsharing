# Протокол проверки сценариев доступа (ПР4)

Дата проверки: 2026-10-21 07:42 (UTC). Сервис: booking-service, `http://127.0.0.1:8010`.
Запросы выполнены скриптом [`booking-service/auth_check.py`](../booking-service/auth_check.py) к запущенному сервису с реальной базой PostgreSQL; учётные записи созданы командой `python manage.py seed_demo`.

Участники проверки:

| От имени | Учётная запись | Роль |
|---|---|---|
| аноним | — (запрос без заголовка `Authorization`) | — |
| клиент Айгерим | aigerim@example.kz | customer |
| клиент Данияр | daniyar@example.kz | customer |
| оператор | operator@carsharing.kz | operator |
| администратор | admin@carsharing.kz | admin |

Любую строку протокола можно повторить вручную, например сценарий 3 (curl, Git Bash):

```bash
B=http://127.0.0.1:8010/api/v1
# вход: в ответе access_token
curl -s -X POST $B/auth/login -H 'Content-Type: application/json' \
     -d '{"email":"aigerim@example.kz","password":"Tulip#Morning2026"}'
CUSTOMER=<access_token клиента>; ADMIN=<access_token администратора>
curl -i -X DELETE $B/cars/<id>                                      # 401
curl -i -X DELETE $B/cars/<id> -H "Authorization: Bearer $CUSTOMER"  # 403
curl -i -X DELETE $B/cars/<id> -H "Authorization: Bearer $ADMIN"     # 204
```

## 1. Регистрация и вход

Новый пользователь регистрируется сам; роль при регистрации всегда `customer`.

| № | Проверка | От имени | Запрос | Ожидается | Получено | Код ошибки | Итог |
|---|---|---|---|---|---|---|---|
| 1.1 | Регистрация | аноним | `POST /api/v1/auth/register` | 201 | **201** | — | ✅ |
| 1.2 | Повторная регистрация того же email (в другом регистре) | аноним | `POST /api/v1/auth/register` | 409 | **409** | `email_taken` | ✅ |
| 1.3 | Слишком короткий пароль | аноним | `POST /api/v1/auth/register` | 400 | **400** | `validation_error` | ✅ |
| 1.4 | Некорректный email | аноним | `POST /api/v1/auth/register` | 400 | **400** | `validation_error` | ✅ |
| 1.5 | Попытка назначить себе роль admin | аноним | `POST /api/v1/auth/register` | 400 | **400** | `validation_error` | ✅ |
| 1.6 | Вход с верным паролем | новый клиент | `POST /api/v1/auth/login` | 200 | **200** | — | ✅ |
| 1.7 | Вход с неверным паролем | новый клиент | `POST /api/v1/auth/login` | 401 | **401** | `invalid_credentials` | ✅ |
| 1.8 | Вход с несуществующим email | аноним | `POST /api/v1/auth/login` | 401 | **401** | `invalid_credentials` | ✅ |
| 1.9 | Кто я (GET /auth/me) по выданному токену | новый клиент | `GET /api/v1/auth/me` | 200 | **200** | — | ✅ |

Ответ — «Регистрация», от имени: аноним:

```http
HTTP/1.1 201

{
  "id": "290b7248-1034-40ce-80ac-748f60d194c0",
  "email": "check3698@example.kz",
  "role": "customer",
  "customer_id": null,
  "is_active": true,
  "created_at": "2026-10-21T07:42:34.384940+00:00",
  "last_login_at": null
}
```

Ответ — «Слишком короткий пароль», от имени: аноним:

```http
HTTP/1.1 400

{
  "error": {
    "code": "validation_error",
    "message": "Validation failed",
    "details": [
      {
        "field": "password",
        "message": "Ensure this field has at least 8 characters."
      }
    ]
  }
}
```

Ответ — «Вход с верным паролем», от имени: новый клиент:

```http
HTTP/1.1 200

{
  "access_token": "eyJhbGciOiJIUzI1NiIsInR5…",
  "token_type": "Bearer",
  "expires_in": 3600,
  "user": {
    "id": "290b7248-1034-40ce-80ac-748f60d194c0",
    "email": "check3698@example.kz",
    "role": "customer",
    "customer_id": null,
    "is_active": true,
    "created_at": "2026-10-21T07:42:34.384940+00:00",
    "last_login_at": "2026-10-21T07:42:34.900218+00:00"
  }
}
```

Ответ — «Вход с неверным паролем», от имени: новый клиент:

```http
HTTP/1.1 401
WWW-Authenticate: Bearer realm="api"

{
  "error": {
    "code": "invalid_credentials",
    "message": "Invalid email or password",
    "details": []
  }
}
```

## 2. Защита маршрутов: проверка токена

Маршрут изменения данных `DELETE /api/v1/cars/{id}`; во всех случаях токен невалиден — ответ 401.

| № | Проверка | От имени | Запрос | Ожидается | Получено | Код ошибки | Итог |
|---|---|---|---|---|---|---|---|
| 2.1 | Без токена | аноним | `DELETE /api/v1/cars/5cbd598f-31e1-4c47-b1f8-0e86df5045bb` | 401 | **401** | `not_authenticated` | ✅ |
| 2.2 | Мусор вместо токена | аноним | `DELETE /api/v1/cars/5cbd598f-31e1-4c47-b1f8-0e86df5045bb` | 401 | **401** | `invalid_token` | ✅ |
| 2.3 | Срок действия истёк | администратор | `DELETE /api/v1/cars/5cbd598f-31e1-4c47-b1f8-0e86df5045bb` | 401 | **401** | `token_expired` | ✅ |
| 2.4 | Подпись чужим ключом (подделка role=admin) | злоумышленник | `DELETE /api/v1/cars/5cbd598f-31e1-4c47-b1f8-0e86df5045bb` | 401 | **401** | `invalid_token` | ✅ |
| 2.5 | Публичный каталог доступен без токена | аноним | `GET /api/v1/cars?limit=1` | 200 | **200** | — | ✅ |

Ответ — «Без токена», от имени: аноним:

```http
HTTP/1.1 401
WWW-Authenticate: Bearer realm="api"

{
  "error": {
    "code": "not_authenticated",
    "message": "Authentication credentials were not provided.",
    "details": []
  }
}
```

Ответ — «Срок действия истёк», от имени: администратор:

```http
HTTP/1.1 401
WWW-Authenticate: Bearer realm="api"

{
  "error": {
    "code": "token_expired",
    "message": "Access token has expired",
    "details": []
  }
}
```

## 3. Удаление машины — только администратор

Одна и та же операция `DELETE /api/v1/cars/{id}` от имени четырёх участников.

| № | Проверка | От имени | Запрос | Ожидается | Получено | Код ошибки | Итог |
|---|---|---|---|---|---|---|---|
| 3.1 | Удалить машину | аноним | `DELETE /api/v1/cars/5cbd598f-31e1-4c47-b1f8-0e86df5045bb` | 401 | **401** | `not_authenticated` | ✅ |
| 3.2 | Удалить машину | клиент Айгерим | `DELETE /api/v1/cars/5cbd598f-31e1-4c47-b1f8-0e86df5045bb` | 403 | **403** | `permission_denied` | ✅ |
| 3.3 | Удалить машину | оператор | `DELETE /api/v1/cars/5cbd598f-31e1-4c47-b1f8-0e86df5045bb` | 403 | **403** | `permission_denied` | ✅ |
| 3.4 | Удалить машину | администратор | `DELETE /api/v1/cars/5cbd598f-31e1-4c47-b1f8-0e86df5045bb` | 204 | **204** | — | ✅ |
| 3.5 | Машина действительно удалена | аноним | `GET /api/v1/cars/5cbd598f-31e1-4c47-b1f8-0e86df5045bb` | 404 | **404** | `car_not_found` | ✅ |

Ответ — «Удалить машину», от имени: клиент Айгерим:

```http
HTTP/1.1 403

{
  "error": {
    "code": "permission_denied",
    "message": "You do not have permission to perform this action",
    "details": [
      {
        "field": "role",
        "message": "Allowed roles: admin; your role: customer"
      }
    ]
  }
}
```

Ответ — «Машина действительно удалена», от имени: аноним:

```http
HTTP/1.1 404

{
  "error": {
    "code": "car_not_found",
    "message": "Car not found",
    "details": [
      {
        "field": "id",
        "message": "No car with id 5cbd598f-31e1-4c47-b1f8-0e86df5045bb"
      }
    ]
  }
}
```

## 4. Решение по водительскому удостоверению — оператор или администратор

Заявка подана новым клиентом из раздела 1; сам владелец заявки одобрить её не может.

| № | Проверка | От имени | Запрос | Ожидается | Получено | Код ошибки | Итог |
|---|---|---|---|---|---|---|---|
| 4.1 | Одобрить ВУ | аноним | `PATCH /api/v1/driver-licenses/be1323e7-34ff-4382-aae4-45c2412a7dac` | 401 | **401** | `not_authenticated` | ✅ |
| 4.2 | Одобрить своё ВУ | новый клиент | `PATCH /api/v1/driver-licenses/be1323e7-34ff-4382-aae4-45c2412a7dac` | 403 | **403** | `permission_denied` | ✅ |
| 4.3 | Одобрить ВУ | оператор | `PATCH /api/v1/driver-licenses/be1323e7-34ff-4382-aae4-45c2412a7dac` | 200 | **200** | — | ✅ |

## 5. Чужая бронь — владелец или персонал

Бронь клиента Айгерим. Роль у Данияра та же (`customer`), но запись не его — ответ 403.

| № | Проверка | От имени | Запрос | Ожидается | Получено | Код ошибки | Итог |
|---|---|---|---|---|---|---|---|
| 5.1 | Посмотреть бронь | аноним | `GET /api/v1/bookings/cbaa991c-1854-44ea-95d9-f1b963037785` | 401 | **401** | `not_authenticated` | ✅ |
| 5.2 | Посмотреть чужую бронь | клиент Данияр | `GET /api/v1/bookings/cbaa991c-1854-44ea-95d9-f1b963037785` | 403 | **403** | `permission_denied` | ✅ |
| 5.3 | Отменить чужую бронь | клиент Данияр | `PATCH /api/v1/bookings/cbaa991c-1854-44ea-95d9-f1b963037785` | 403 | **403** | `permission_denied` | ✅ |
| 5.4 | Посмотреть свою бронь | клиент Айгерим | `GET /api/v1/bookings/cbaa991c-1854-44ea-95d9-f1b963037785` | 200 | **200** | — | ✅ |
| 5.5 | Посмотреть бронь | оператор | `GET /api/v1/bookings/cbaa991c-1854-44ea-95d9-f1b963037785` | 200 | **200** | — | ✅ |

Ответ — «Посмотреть чужую бронь», от имени: клиент Данияр:

```http
HTTP/1.1 403

{
  "error": {
    "code": "permission_denied",
    "message": "You can access only your own data",
    "details": [
      {
        "field": "customer_id",
        "message": "Record belongs to another customer"
      }
    ]
  }
}
```

## 6. Управление пользователями — только администратор

| № | Проверка | От имени | Запрос | Ожидается | Получено | Код ошибки | Итог |
|---|---|---|---|---|---|---|---|
| 6.1 | Список пользователей | аноним | `GET /api/v1/users` | 401 | **401** | `not_authenticated` | ✅ |
| 6.2 | Список пользователей | клиент Айгерим | `GET /api/v1/users` | 403 | **403** | `permission_denied` | ✅ |
| 6.3 | Список пользователей | оператор | `GET /api/v1/users` | 403 | **403** | `permission_denied` | ✅ |
| 6.4 | Список пользователей | администратор | `GET /api/v1/users?limit=2` | 200 | **200** | — | ✅ |

## 7. Единый формат ошибок

Все ответы с ошибкой имеют вид `{"error": {"code", "message", "details"}}`.

| № | Проверка | От имени | Запрос | Ожидается | Получено | Код ошибки | Итог |
|---|---|---|---|---|---|---|---|
| 7.1 | Неизвестный URL | аноним | `GET /api/v1/no-such-resource` | 404 | **404** | `not_found` | ✅ |
| 7.2 | Неподдерживаемый метод | администратор | `PUT /api/v1/cars` | 405 | **405** | `method_not_allowed` | ✅ |
| 7.3 | Битый JSON | администратор | `POST /api/v1/cars` | 400 | **400** | `parse_error` | ✅ |
| 7.4 | Не JSON | администратор | `POST /api/v1/cars` | 415 | **415** | `unsupported_media_type` | ✅ |

Ответ — «Неизвестный URL», от имени: аноним:

```http
HTTP/1.1 404

{
  "error": {
    "code": "not_found",
    "message": "Resource not found",
    "details": [
      {
        "field": "path",
        "message": "/api/v1/no-such-resource"
      }
    ]
  }
}
```

## Итог

Выполнено проверок: 35, совпало с ожидаемым: 35, расхождений: 0.

Права реально различаются: одна и та же операция даёт 401 без токена, 403 для роли без прав (или для чужой записи) и успешный ответ для роли, которой действие разрешено.
