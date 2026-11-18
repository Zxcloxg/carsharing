"""Очередь фоновых задач в PostgreSQL.

Одна строка таблицы background_tasks — одна поставленная задача: что
выполнить (путь к функции-задаче и аргументы), когда (run_after), сколько
попыток уже было и чем закончилась последняя. Задача ставится в очередь
INSERT-ом в той же транзакции, что и бизнес-изменение, поэтому не бывает ни
«потерянной» задачи при успешной операции, ни задачи для отменённой
операции (подробно — docs/async-scenario.md).
"""
import uuid

from django.db import models
from django.db.models import F, Q
from django.tasks import TaskResultStatus


class BackgroundTask(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    task_path = models.CharField(max_length=200, db_comment="Модуль и имя функции-задачи, например fleet.tasks.charge_payment")
    queue_name = models.CharField(max_length=32, default="default")
    priority = models.SmallIntegerField(default=0, db_comment="Больше — раньше; от -100 до 100")
    args = models.JSONField(default=list)
    kwargs = models.JSONField(default=dict)
    status = models.CharField(max_length=10, choices=TaskResultStatus.choices, default=TaskResultStatus.READY)
    attempts = models.PositiveSmallIntegerField(default=0, db_comment="Сколько раз задачу уже начинали выполнять")
    max_attempts = models.PositiveSmallIntegerField(db_comment="Предел попыток, после которого задача — FAILED")
    run_after = models.DateTimeField(db_comment="Не раньше этого момента: время постановки или следующей попытки")
    enqueued_at = models.DateTimeField(auto_now_add=True)
    started_at = models.DateTimeField(null=True, blank=True, db_comment="Начало первой попытки")
    last_attempted_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(null=True, blank=True, db_comment="Причина последнего сбоя — кратко")
    errors = models.JSONField(default=list, db_comment="Все сбои: номер попытки, класс исключения, текст, стектрейс")
    return_value = models.JSONField(null=True, blank=True)
    worker_ids = models.JSONField(default=list, db_comment="Обработчики, бравшие задачу, по попыткам")

    class Meta:
        db_table = "background_tasks"
        db_table_comment = "Очередь фоновых задач и их результаты"
        ordering = ["enqueued_at"]
        constraints = [
            models.CheckConstraint(condition=Q(status__in=TaskResultStatus.values), name="background_tasks_status_valid"),
            models.CheckConstraint(condition=Q(priority__gte=-100, priority__lte=100), name="background_tasks_priority_range"),
            models.CheckConstraint(condition=Q(max_attempts__gte=1), name="background_tasks_max_attempts_min"),
            models.CheckConstraint(condition=Q(attempts__lte=F("max_attempts")), name="background_tasks_attempts_limit"),
            # Завершённая задача (успех или окончательный сбой) обязательно с временем завершения, остальные — без.
            models.CheckConstraint(
                condition=Q(status__in=["SUCCESSFUL", "FAILED"], finished_at__isnull=False)
                | (~Q(status__in=["SUCCESSFUL", "FAILED"]) & Q(finished_at__isnull=True)),
                name="background_tasks_finished_at_matches_status",
            ),
        ]
        indexes = [
            # Выборка обработчика: готовые задачи очереди по приоритету и времени. Частичный индекс
            # хранит только READY-строки — выполненные задачи его не раздувают.
            models.Index(
                F("queue_name"), F("priority").desc(), F("run_after"),
                condition=Q(status="READY"), name="background_tasks_ready_idx",
            ),
            models.Index(fields=["status", "enqueued_at"], name="background_tasks_status_idx"),
        ]

    def __str__(self):
        return f"{self.task_path} [{self.status}] {self.attempts}/{self.max_attempts}"
