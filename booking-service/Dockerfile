# Образ booking-service (ПР7). Один образ — четыре роли (docker/entrypoint.sh):
#   api      — REST API под gunicorn (по умолчанию)
#   worker   — обработчик фоновых задач (ПР6)
#   migrate  — миграции схемы (+ демо-данные при SEED_DEMO=true), запускается один раз при развёртывании
#   provider — эмулятор платёжного провайдера для локального стенда
# Сборка: docker build -t carsharing/booking-service booking-service

# Официальный образ Python той же версии, что и при разработке; slim — без компиляторов и документации.
FROM python:3.12-slim

# Не писать .pyc, сразу выводить логи (без буфера), pip без кэша и проверки обновлений.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Отдельный системный пользователь без пароля и оболочки: процесс в контейнере не работает от root.
RUN groupadd --system app && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app

# Зависимости — отдельным слоем до копирования кода: пока requirements.txt не менялся,
# Docker берёт этот слой из кэша, и пересборка после правки кода занимает секунды.
COPY requirements.txt .
RUN pip install -r requirements.txt

# Код приложения (лишнее отсекает .dockerignore: .venv, .env, тесты, отчёты покрытия).
COPY . .

# Статика админки собирается при сборке образа. Настройкам нужны переменные окружения, поэтому
# здесь подставлены одноразовые заглушки только для этой команды — в образ (ENV) они не попадают.
RUN DJANGO_SECRET_KEY=build-time-placeholder-not-used-at-runtime-0123456789abcdef \
    JWT_SECRET=build-time-placeholder-not-used-at-runtime \
    DB_HOST=unused DB_PORT=5432 DB_NAME=unused DB_USER=unused DB_PASSWORD=unused \
    python manage.py collectstatic --noinput --verbosity 0 \
 && sed -i 's/\r$//' docker/entrypoint.sh \
 && chmod 0755 docker/entrypoint.sh

USER app

# Порт API (gunicorn, gunicorn.conf.py). Эмулятор провайдера в роли provider слушает 8030.
EXPOSE 8010

ENTRYPOINT ["/app/docker/entrypoint.sh"]
CMD ["api"]
