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

## Эндпоинты (скелет)
| Метод | Путь | Назначение |
|---|---|---|
| GET | /health | Проверка живости сервиса |
| —  | /admin/ | Админка Django (для операторов) |

Доменные эндпоинты (/api/...) добавляются в следующих практических работах.
