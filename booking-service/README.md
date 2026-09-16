# booking-service (Django + DRF)

Ядро бизнес-домена каршеринга: пользователи, верификация ВУ, автопарк,
брони/поездки, тарифы, платежи. Публичный REST для мобильного клиента +
админка для операторов.

## Запуск
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py runserver 8010
```

## Эндпоинты
| Метод | Путь | Назначение |
|---|---|---|
| GET | /health | Проверка живости сервиса |
| GET | /api/v1/cars | Список машин: пагинация `page`/`limit`, фильтры `status`, `brand`, `max_price_per_minute` |
| POST | /api/v1/cars | Добавить машину (201 + `Location`) |
| GET | /api/v1/cars/{id} | Получить машину |
| PUT | /api/v1/cars/{id} | Полностью заменить данные машины |
| PATCH | /api/v1/cars/{id} | Частично обновить (например, статус) |
| DELETE | /api/v1/cars/{id} | Убрать машину из автопарка (204) |
| —  | /admin/ | Админка Django (для операторов) |

Формальный контракт: [`openapi.yaml`](openapi.yaml). Описание сущности, URI и
формата ошибок: [`../docs/api.md`](../docs/api.md). Примеры запросов и ответов:
[`../docs/examples.http`](../docs/examples.http).

## Структура приложения `fleet`
Слои разделены: маршрут -> бизнес-логика -> хранение.

| Файл | Ответственность |
|---|---|
| `fleet/urls.py` | Пути ресурса |
| `fleet/views.py` | Маршруты: разбор запроса и коды ответов |
| `fleet/serializers.py` | Валидация входных данных (схемы/DTO) |
| `fleet/services.py` | Бизнес-логика: уникальность номера, фильтры, пагинация |
| `fleet/repository.py` | Хранение (в ПР2 — в памяти, в ПР3 — PostgreSQL) |
| `fleet/errors.py` | Доменные исключения и единый формат ошибок |

## Тесты
```bash
python manage.py test fleet
```
