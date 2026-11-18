"""Бэкенд django.tasks, хранящий очередь в PostgreSQL.

Django 6 даёт стандартный API фоновых задач (декоратор @task, Task.enqueue(),
TaskResult со статусом), но в поставке только два бэкенда: ImmediateBackend
выполняет задачу сразу внутри запроса, DummyBackend никогда её не выполняет.
Этот бэкенд реализует тот же интерфейс поверх таблицы background_tasks, а
выполняет задачи отдельный процесс — python manage.py run_worker (jobs/worker.py).

Настройки (settings.TASKS["default"]["OPTIONS"]):
* MAX_ATTEMPTS — сколько раз пробовать задачу, прежде чем признать сбой;
* RETRY_BACKOFF_SECONDS, RETRY_BACKOFF_MAX_SECONDS — пауза перед повтором
  (экспоненциальная, jobs/retry.py);
* LEASE_SECONDS — через сколько секунд «зависшую» задачу (обработчик упал
  посреди выполнения) можно забрать другому обработчику.
"""
import logging
import uuid

from django.tasks import TaskResult, TaskResultStatus
from django.tasks.backends.base import BaseTaskBackend
from django.tasks.base import TaskError
from django.tasks.exceptions import TaskResultDoesNotExist
from django.tasks.signals import task_enqueued
from django.utils import timezone
from django.utils.json import normalize_json
from django.utils.module_loading import import_string

from .models import BackgroundTask

logger = logging.getLogger("jobs")


def short_id(task_id):
    """Первые 8 символов идентификатора — достаточно, чтобы различать задачи в логе."""
    return str(task_id)[:8]


def resolve_task(task_path):
    """Объект Task по пути, сохранённому в очереди (fleet.tasks.charge_payment)."""
    return import_string(task_path)


def to_task_result(row, backend_alias, task=None):
    """Строка очереди -> стандартный TaskResult из django.tasks."""
    result = TaskResult(
        task=task or resolve_task(row.task_path),
        id=str(row.id),
        status=TaskResultStatus(row.status),
        enqueued_at=row.enqueued_at,
        started_at=row.started_at,
        finished_at=row.finished_at,
        last_attempted_at=row.last_attempted_at,
        args=row.args,
        kwargs=row.kwargs,
        backend=backend_alias,
        errors=[
            TaskError(exception_class_path=error["exception_class_path"], traceback=error["traceback"])
            for error in row.errors
        ],
        worker_ids=list(row.worker_ids),
    )
    object.__setattr__(result, "_return_value", row.return_value)
    return result


class DatabaseBackend(BaseTaskBackend):
    supports_defer = True  # run_after: отложенный запуск и пауза перед повтором
    supports_get_result = True  # статус задачи читается из любого процесса
    supports_priority = True

    def __init__(self, alias, params):
        super().__init__(alias, params)
        self.max_attempts = int(self.options.get("MAX_ATTEMPTS", 3))
        self.retry_backoff_seconds = float(self.options.get("RETRY_BACKOFF_SECONDS", 2))
        self.retry_backoff_max_seconds = float(self.options.get("RETRY_BACKOFF_MAX_SECONDS", 300))
        self.lease_seconds = float(self.options.get("LEASE_SECONDS", 300))
        if self.max_attempts < 1:
            raise ValueError("MAX_ATTEMPTS must be at least 1")

    def enqueue(self, task, args, kwargs):
        """Поставить задачу в очередь — один INSERT в текущей транзакции вызывающего кода.

        Если вызывающая транзакция откатится, исчезнет и задача; обработчик видит
        задачу только после COMMIT, то есть вместе с данными, которые ей нужны.
        """
        self.validate_task(task)
        row = BackgroundTask.objects.create(
            task_path=task.module_path,
            queue_name=task.queue_name,
            priority=task.priority,
            args=normalize_json(list(args)),
            kwargs=normalize_json(dict(kwargs)),
            max_attempts=self.max_attempts,
            run_after=task.run_after or timezone.now(),
        )
        result = to_task_result(row, self.alias, task)
        task_enqueued.send(type(self), task_result=result)
        logger.info(
            "Задача %s поставлена в очередь: id=%s, аргументы %s, попыток не больше %d",
            task.name, short_id(row.id), _format_arguments(row.args, row.kwargs), row.max_attempts,
        )
        return result

    def get_result(self, result_id):
        try:
            row = BackgroundTask.objects.filter(pk=uuid.UUID(str(result_id))).first()
        except ValueError:
            row = None
        if row is None:
            raise TaskResultDoesNotExist(result_id)
        return to_task_result(row, self.alias)


def _format_arguments(args, kwargs):
    parts = [repr(value) for value in args] + [f"{key}={value}" for key, value in kwargs.items()]
    return ", ".join(parts) or "—"
