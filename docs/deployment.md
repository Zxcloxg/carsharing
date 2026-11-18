# Контейнеризация, развёртывание и наблюдаемость (ПР7)

Протокол проверки системы «с нуля» в контейнерах — [`deploy-check.md`](deploy-check.md).

## 1. Запуск одной командой

```bash
cp .env.example .env          # заполнить DB_PASSWORD, DJANGO_SECRET_KEY, JWT_SECRET (и при желании METRICS_TOKEN)
docker compose up -d --build --wait
```

| Сервис | Образ | Что делает | Порт на хосте |
|---|---|---|---|
| `db` | `postgres:17-alpine` | PostgreSQL, данные — в именованном томе `pgdata` | — (только внутренняя сеть) |
| `migrate` | `carsharing/booking-service` | Одноразовый шаг: `migrate` (+ `seed_demo` при `SEED_DEMO=true`), затем завершается с кодом 0 | — |
| `api` | `carsharing/booking-service` | REST API под gunicorn | 8010 |
| `worker` | `carsharing/booking-service` | Обработчик фоновых задач (ПР6) | — |
| `payment-provider` | `carsharing/booking-service` | Эмулятор платёжного провайдера (ПР6) | — |
| `telemetry` | `carsharing/telemetry-service` | Flask-сервис телеметрии под gunicorn | 8020 |

Порядок старта задают `depends_on` с условиями: `db` должен быть **healthy** (`pg_isready`),
`migrate` — **завершиться успешно**, и только тогда стартуют `api` и `worker`. Поэтому API
никогда не работает со схемой, к которой ещё не применены миграции. Все сервисы — в одной
сети `backend` и обращаются друг к другу по имени сервиса: `DB_HOST=db`,
`PAYMENT_PROVIDER_URL=http://payment-provider:8030`.

Полезные команды:

```bash
docker compose ps                       # состояние и health каждого контейнера
docker compose logs -f api worker       # JSON-логи
docker compose stop db                  # имитация падения базы: /health -> 503, api -> unhealthy
docker compose run --rm api python manage.py create_user boss@carsharing.kz --role admin
docker compose down                     # остановить, данные в томе pgdata сохраняются
docker compose down -v                  # остановить и удалить данные
```

## 2. Dockerfile booking-service — построчно

| Строки | Зачем |
|---|---|
| `FROM python:3.12-slim` | Официальный образ той же версии Python, что и при разработке; `slim` — без компиляторов и документации (образ 287 МБ) |
| `ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 …` | Не писать `.pyc` в контейнер, выводить логи сразу (иначе `docker logs` отстаёт), не хранить кэш pip в образе |
| `RUN groupadd … && useradd --system …` | Системный пользователь `app` без пароля и оболочки |
| `COPY requirements.txt .` + `RUN pip install -r requirements.txt` | Зависимости — **отдельным слоем до кода**: пока `requirements.txt` не менялся, слой берётся из кэша и пересборка после правки кода занимает секунды |
| `COPY . .` | Код; лишнее (`.venv`, `.env`, тесты, отчёты покрытия) отсекает `.dockerignore` |
| `RUN DJANGO_SECRET_KEY=placeholder … collectstatic` | Статика админки собирается при сборке. Заглушки нужны только этой команде и в `ENV` образа не попадают (это проверяет `deploy_check.py`) |
| `USER app` | Процессы контейнера работают **не от root** |
| `EXPOSE 8010` | Порт API |
| `ENTRYPOINT ["/app/docker/entrypoint.sh"]`, `CMD ["api"]` | Один образ — четыре роли: `api` (gunicorn), `worker`, `migrate`, `provider` |

API запускается **gunicorn** (`gunicorn.conf.py`), а не `runserver`: без автоперезагрузки и
отладки, с пулом потоков, тайм-аутом зависшего запроса и корректной остановкой. Статику
админки раздаёт WhiteNoise.

## 3. Конфигурация и секреты

* Все параметры передаются через переменные окружения. Список — в корневом
  [`.env.example`](../.env.example) (для compose; значения секретов пустые) и в
  [`booking-service/.env.example`](../booking-service/.env.example) (для локального запуска).
* Реальный `.env` в `.gitignore`, в образ его не пускает `.dockerignore`.
* В `docker-compose.yml` секретов нет: только ссылки `${DB_PASSWORD:?…}`. Если переменная не
  задана, `docker compose` останавливается с понятной ошибкой, а не запускает систему с пустым
  паролем.
* Без `DJANGO_SECRET_KEY` (не короче 50 символов) и `JWT_SECRET` (не короче 32) сервис не стартует
  (ПР5).
* В CI секреты генерируются случайно на каждый запуск (`secrets.token_urlsafe`).

## 4. Миграции при развёртывании

Отдельный сервис `migrate` из того же образа выполняет `python manage.py migrate --noinput`
(и `seed_demo` при `SEED_DEMO=true`). Миграции не встроены в старт API, и это сделано
намеренно. При нескольких экземплярах API или при перезапуске worker миграции не запускаются
параллельно, а падение миграции видно как отдельный сбойный шаг (`Exited (1)`). Повторный
запуск безопасен: Django пропускает уже применённые миграции, `seed_demo` не дублирует данные.

## 5. Проверка состояния

| Эндпоинт | Что проверяет | Ответ |
|---|---|---|
| `GET /health` | Готовность: процесс жив **и** база отвечает на `SELECT 1`; заодно — очередь ПР6 (сколько задач ждёт, сколько упало, возраст самой старой) | 200 `{"status": "ok", "checks": {...}}` или **503** `{"status": "unavailable", ...}` |
| `GET /health/live` | Живость: только процесс, без зависимостей | 200 |

`healthcheck` в `docker-compose.yml` обращается к `/health` каждые 10 с. После трёх неудач
контейнер `api` получает статус `unhealthy`. Когда база возвращается, тот же процесс API снова
отвечает 200: сломанное соединение закрывается, и следующий запрос подключается заново.
Если контейнер базы остановлен, его имя `db` исчезает из DNS сети compose, и ответ 503 приходит
через несколько секунд: в протоколе — около 8 с, столько занимает неудачный поиск имени.
Healthcheck с тайм-аутом 5 с считает такую попытку неудачной, поэтому статус `unhealthy`
выставляется так же, как при быстром 503. Причину
недоступности базы (имя хоста, текст ошибки драйвера) видно только в логе. Наружу отдаётся
класс ошибки без деталей.

## 6. Наблюдаемость

**Логи.** `LOG_FORMAT=json`: каждая запись — одна строка JSON в stdout контейнера.

```json
{"time": "2026-11-18T08:21:04.512+00:00", "level": "INFO", "logger": "config.requests",
 "message": "GET /api/v1/cars -> 200 за 23.4 мс", "request_id": "deploy-check-probe-1",
 "method": "GET", "path": "/api/v1/cars", "route": "/api/v1/cars", "status": 200, "duration_ms": 23.4}
```

* **request_id.** Берётся из заголовка `X-Request-ID` (его ставит API Gateway) или генерируется.
  Возвращается в ответе тем же заголовком и попадает во все строки лога этого запроса. Клиент
  сообщает id из ответа — и по нему находится вся история запроса. Значение проверяется
  (`[A-Za-z0-9._-]{1,64}`), поэтому подделать через заголовок строку лога нельзя.
* **task_id.** Все записи обработчика очереди (ПР6), пока он выполняет задачу, несут `task_id`.
* **Строка на запрос.** Метод, путь, шаблон маршрута, код ответа и длительность. Запросы
  `/health` и `/metrics` пишутся только на уровне DEBUG, чтобы проверки каждые 10 с не забивали
  лог.
* Локально (`LOG_FORMAT=plain`) — читаемый текст: `[время] INFO логгер: сообщение [request_id]`.

**Метрики.** `GET /metrics` в формате Prometheus. Если задан `METRICS_TOKEN`, нужен заголовок
`Authorization: Bearer <токен>`.

| Метрика | Тип | Смысл |
|---|---|---|
| `http_requests_total{method,route,status}` | counter | Запросы по маршруту и коду: рост 5xx или 401 виден сразу |
| `http_request_duration_seconds{route}` | histogram | Время ответа: бакеты, сумма и количество |
| `http_requests_in_flight`, `process_uptime_seconds` | gauge | Нагрузка сейчас и время с перезапуска |
| `background_tasks{status}` | gauge | Очередь ПР6: растущий `READY` означает, что worker не работает, растущий `FAILED` — что провайдер сбоит |
| `payments{status}` | gauge | Платежи: `pending`, `paid`, `failed` |
| `database_up` | gauge | 1 — база отвечает, 0 — нет |

Счётчики HTTP хранятся в памяти процесса. Поэтому API работает одним процессом gunicorn с
пулом из 4 потоков, и метрики остаются цельными. Метрики очереди и платежей читаются из базы
при каждом запросе к `/metrics`.

## 7. CI: GitHub Actions

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) запускается на каждый push и pull request.

| Job | Шаги |
|---|---|
| `test` | checkout → Python 3.12 с кэшем pip → случайные секреты → `pip install` → **линтер** `ruff check .` → `makemigrations --check` (модели и миграции не разошлись) → **тесты** `coverage run manage.py test tests --shuffle` на временной PostgreSQL → `coverage report --fail-under=95` |
| `docker` (после `test`) | `.env` со случайными секретами → `docker compose build` → `docker compose up -d --wait` (миграции, healthcheck всех сервисов) → `curl` `/health` обоих сервисов, вход администратора, каталог машин → при сбое — логи контейнеров → `docker compose down -v` |

Если падает хотя бы один тест, падает шаг «Тесты», job `test` получает статус failure, а job
`docker` не запускается (`needs: test`). Коммит на GitHub помечается красным крестом, бейдж в
README становится красным. Линтер настроен в
[`booking-service/ruff.toml`](../booking-service/ruff.toml): ошибки pyflakes и стиля, порядок
импортов, типичные ошибки (flake8-bugbear).
