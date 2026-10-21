"""Общие помощники слоя доступа к данным."""
import uuid

from django.db import IntegrityError, transaction


def parse_id(value):
    """UUID из пути запроса; некорректная строка означает «такой записи нет»."""
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


def iso(value):
    return value.isoformat() if value else None


def save_in_savepoint(instance, violations):
    """Сохраняет запись в точке сохранения и переводит нарушения ограничений базы в доменные ошибки.

    `violations` — словарь {имя ограничения: фабрика исключения}. Точка сохранения
    нужна, чтобы нарушение UNIQUE не ломало внешнюю транзакцию сервиса.
    """
    try:
        with transaction.atomic():
            instance.save()
    except IntegrityError as exc:
        for constraint, make_error in violations.items():
            if constraint in str(exc):
                raise make_error() from exc
        raise
    return instance
