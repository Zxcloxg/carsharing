"""Политика повторов фоновых задач — чистые функции, без базы и сети.

Любая ошибка задачи по умолчанию считается временной (сеть, тайм-аут,
перегрузка внешнего сервиса): задача повторяется с растущей паузой, пока не
кончатся попытки. Ошибка, которую повтор не исправит (отказ банка, удалённая
запись), наследует PermanentTaskError — тогда задача сразу завершается
сбоем, не тратя попытки и не нагружая внешний сервис.
"""


class PermanentTaskError(Exception):
    """Сбой, который не исчезнет при повторе: задачу нужно завершить сразу."""


def backoff_delay(attempt, base_seconds, max_seconds):
    """Пауза перед следующей попыткой после неудачной попытки номер `attempt` (с 1).

    Экспоненциальная: base, 2·base, 4·base, … но не больше max_seconds. Растущая
    пауза даёт упавшему сервису время подняться и не добивает его потоком повторов.
    """
    if attempt < 1:
        raise ValueError("attempt starts from 1")
    return min(base_seconds * 2 ** (attempt - 1), max_seconds)


def is_final_failure(exc, attempt, max_attempts):
    """Сбой окончательный: ошибка не временная или попытки исчерпаны."""
    return isinstance(exc, PermanentTaskError) or attempt >= max_attempts


def describe_error(exc):
    """Краткая причина сбоя для лога и поля last_error: «Класс: текст»."""
    text = str(exc).strip()
    return f"{type(exc).__name__}: {text}" if text else type(exc).__name__
