"""Проверка подключения к базе данных при старте сервиса."""
import logging

from django.db import connection

logger = logging.getLogger(__name__)


def log_database_connection():
    """Подключается к БД и явно пишет в лог, куда именно; при ошибке сервис не стартует."""
    params = connection.settings_dict
    target = f"{params['USER']}@{params['HOST']}:{params['PORT']}/{params['NAME']}"
    try:
        with connection.cursor() as cursor:
            cursor.execute("SHOW server_version")
            (version,) = cursor.fetchone()
    except Exception:
        logger.exception("Не удалось подключиться к базе данных %s", target)
        raise
    logger.info("Подключение к базе данных установлено: PostgreSQL %s, %s", version, target)
    connection.close()
