"""Чтение очереди для API и других модулей: статус задачи, список задач.

Наружу задача отдаётся без стектрейсов: в API — номер попытки, время и
краткая причина сбоя; полный стектрейс хранится в базе и виден в админке.
"""
import math
import uuid

from django.tasks import TaskResultStatus
from rest_framework import status

from config.exceptions import DomainError

from .models import BackgroundTask


class TaskNotFound(DomainError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "task_not_found"
    message = "Background task not found"


def _iso(value):
    return value.isoformat() if value else None


def to_dict(row):
    return {
        "id": str(row.id),
        "task": row.task_path.rsplit(".", 1)[-1],
        "queue": row.queue_name,
        "status": row.status,
        "attempts": row.attempts,
        "max_attempts": row.max_attempts,
        "args": row.args,
        "kwargs": row.kwargs,
        "enqueued_at": _iso(row.enqueued_at),
        "started_at": _iso(row.started_at),
        "last_attempted_at": _iso(row.last_attempted_at),
        # Когда обработчик возьмёт задачу в следующий раз (для ожидающих и повторяемых).
        "next_attempt_at": _iso(row.run_after) if row.status == TaskResultStatus.READY else None,
        "finished_at": _iso(row.finished_at),
        "last_error": row.last_error,
        "errors": [{"attempt": e.get("attempt"), "at": e.get("at"), "message": e.get("message")} for e in row.errors],
        "return_value": row.return_value,
    }


def _find(task_id):
    try:
        return BackgroundTask.objects.filter(pk=uuid.UUID(str(task_id))).first()
    except ValueError:
        return None


def get_task(task_id):
    row = _find(task_id)
    if row is None:
        raise TaskNotFound(details=[{"field": "id", "message": f"No task with id {task_id}"}])
    return to_dict(row)


def find_task(task_id):
    """Задача или None — для вложенного статуса в ответе о платеже."""
    row = _find(task_id) if task_id else None
    return to_dict(row) if row else None


def list_tasks(*, page=1, limit=10, status=None, task=None):
    query = BackgroundTask.objects.all()
    if status:
        query = query.filter(status=status)
    if task:
        query = query.filter(task_path__endswith=f".{task}")
    total = query.count()
    offset = (page - 1) * limit
    items = [to_dict(row) for row in query.order_by("-enqueued_at")[offset:offset + limit]]
    return {"items": items, "page": page, "limit": limit, "total": total, "pages": math.ceil(total / limit) if total else 0}
