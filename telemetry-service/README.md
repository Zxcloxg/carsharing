# telemetry-service (Flask)

Приём телеметрии от автомобилей и поиск ближайших свободных машин в
реальном времени. Лёгкий сервис на пару высокочастотных эндпоинтов.

## Запуск
```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python app.py            # http://127.0.0.1:8020
```

## Эндпоинты
| Метод | Путь | Назначение |
|---|---|---|
| GET  | /health | Проверка живости сервиса |
| POST | /telemetry | Приём координат/состояния от машины |
| GET  | /cars/nearest | Ближайшие свободные машины (internal, для booking-service) |

В скелете позиции хранятся в памяти; в проде — Redis.
