# Образ telemetry-service (ПР7): Flask под gunicorn, не от root.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
RUN groupadd --system app && useradd --system --gid app --home-dir /app --shell /usr/sbin/nologin app

COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app.py .

USER app
EXPOSE 8020
# Один процесс: последние координаты машин пока хранятся в памяти процесса (в плане — Redis),
# и несколько процессов видели бы разные данные. Параллельность — потоками.
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8020", "--workers", "1", "--worker-class", "gthread", "--threads", "4"]
