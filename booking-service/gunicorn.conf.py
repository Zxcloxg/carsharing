"""Настройки gunicorn — production-сервера API в контейнере (ПР7).

Один процесс с пулом потоков: метрики /metrics считаются в памяти процесса,
и при одном процессе они цельные; стенду с 1 ГБ памяти этого хватает.
Под нагрузкой увеличивают GUNICORN_WORKERS, а метрики собирают с каждого
экземпляра (или переводят на общий сборщик).
"""
import os

bind = f"0.0.0.0:{os.environ.get('PORT', '8010')}"
workers = int(os.environ.get("GUNICORN_WORKERS", "1"))
worker_class = "gthread"
threads = int(os.environ.get("GUNICORN_THREADS", "4"))
timeout = 30  # зависший запрос обрывается, поток освобождается
graceful_timeout = 20  # при остановке контейнера даём запросам завершиться
forwarded_allow_ips = "*"  # доверие X-Forwarded-* задаёт приложение (TRUSTED_PROXY_COUNT)

# Строку на каждый запрос пишет само приложение (config.observability) — с request_id и в JSON,
# поэтому access-лог gunicorn выключен, а его служебные сообщения тоже идут в JSON.
accesslog = None
logconfig_dict = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "config.observability.JsonFormatter"}},
    "handlers": {"console": {"class": "logging.StreamHandler", "formatter": "json", "stream": "ext://sys.stdout"}},
    "loggers": {
        "gunicorn.error": {"handlers": ["console"], "level": "INFO", "propagate": False},
        "gunicorn.access": {"handlers": [], "level": "WARNING", "propagate": False},
    },
}
