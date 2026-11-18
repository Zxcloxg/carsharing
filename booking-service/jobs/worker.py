"""Обработчик очереди: забирает готовые задачи из background_tasks и выполняет их.

Цикл обработчика:
1. claim() — в короткой транзакции выбрать одну готовую задачу
   (SELECT … FOR UPDATE SKIP LOCKED), перевести её в RUNNING и увеличить
   счётчик попыток. SKIP LOCKED позволяет запустить несколько обработчиков:
   строку, которую уже взял один, другие просто пропускают;
2. process() — вызвать функцию задачи вне транзакции очереди (внешний вызов
   не держит блокировку строки и соединение в транзакции);
3. по результату — SUCCESSFUL с возвращённым значением, либо READY с
   run_after = сейчас + пауза (повтор), либо FAILED, если ошибка не временная
   или попытки исчерпаны. О каждом исходе — запись в логе «jobs».

Задачу в состоянии RUNNING, которую обработчик не завершил за LEASE_SECONDS
(процесс убит, машина перезагружена), забирает следующий обработчик —
задача не теряется. Поэтому задачи должны быть идемпотентными: повторное
выполнение не должно повторять эффект (у платежа — ключ идемпотентности).
"""
import logging
import os
import socket
import time
from datetime import timedelta
from traceback import format_exception

from django.db import DatabaseError, close_old_connections, transaction
from django.db.models import Q
from django.tasks import TaskContext, TaskResultStatus, task_backends
from django.tasks.signals import task_finished, task_started
from django.utils import timezone
from django.utils.json import normalize_json

from .backends import resolve_task, short_id, to_task_result
from .models import BackgroundTask
from .retry import PermanentTaskError, backoff_delay, describe_error, is_final_failure

logger = logging.getLogger("jobs")


class Worker:
    def __init__(self, backend_alias="default", queues=None, worker_id=None):
        self.backend = task_backends[backend_alias]
        self.queues = sorted(queues or self.backend.queues)
        self.worker_id = worker_id or f"{socket.gethostname()}:{os.getpid()}"
        self._stopping = False

    # --- выбор задачи -------------------------------------------------------
    def claim(self):
        """Забрать одну готовую задачу или None, если делать нечего."""
        now = timezone.now()
        stale_before = now - timedelta(seconds=self.backend.lease_seconds)
        with transaction.atomic():
            row = (
                BackgroundTask.objects.select_for_update(skip_locked=True)
                .filter(queue_name__in=self.queues)
                .filter(
                    Q(status=TaskResultStatus.READY, run_after__lte=now)
                    | Q(status=TaskResultStatus.RUNNING, last_attempted_at__lt=stale_before)
                )
                .order_by("-priority", "run_after")
                .first()
            )
            if row is None:
                return None
            if row.status == TaskResultStatus.RUNNING:
                logger.warning(
                    "Задача %s (%s) зависла: обработчик %s не завершил её за %.0f с — забираю",
                    short_id(row.id), _name(row), row.worker_ids[-1] if row.worker_ids else "?",
                    self.backend.lease_seconds,
                )
                if row.attempts >= row.max_attempts:
                    self._finish_failed(row, "Lease expired: worker stopped during the last attempt", now)
                    return None
            row.status = TaskResultStatus.RUNNING
            row.attempts += 1
            row.started_at = row.started_at or now
            row.last_attempted_at = now
            row.worker_ids = [*row.worker_ids, self.worker_id]
            row.save(update_fields=["status", "attempts", "started_at", "last_attempted_at", "worker_ids"])
        return row

    # --- выполнение -----------------------------------------------------------
    def process(self, row):
        """Выполнить взятую задачу и записать исход: успех, повтор или окончательный сбой."""
        try:
            task = resolve_task(row.task_path)
        except ImportError as exc:  # код задачи удалён или переименован — повтор не поможет
            self._handle_failure(row, PermanentTaskError(f"Unknown task {row.task_path}: {exc}"), 0.0)
            return
        task_result = to_task_result(row, self.backend.alias, task)
        task_started.send(type(self.backend), task_result=task_result)
        logger.info(
            "Задача %s (%s) — попытка %d из %d, обработчик %s",
            short_id(row.id), task.name, row.attempts, row.max_attempts, self.worker_id,
        )
        started = time.monotonic()
        try:
            if task.takes_context:
                value = task.call(TaskContext(task_result=task_result), *row.args, **row.kwargs)
            else:
                value = task.call(*row.args, **row.kwargs)
            value = normalize_json(value)
        except KeyboardInterrupt:
            raise
        except Exception as exc:
            self._handle_failure(row, exc, time.monotonic() - started)
        else:
            self._handle_success(row, value, time.monotonic() - started)

    def _handle_success(self, row, value, elapsed):
        row.status = TaskResultStatus.SUCCESSFUL
        row.finished_at = timezone.now()
        row.return_value = value
        row.save(update_fields=["status", "finished_at", "return_value"])
        logger.info(
            "Задача %s (%s) выполнена за %.2f с с попытки %d: %s",
            short_id(row.id), _name(row), elapsed, row.attempts, value,
        )
        self._notify_finished(row)

    def _handle_failure(self, row, exc, elapsed):
        reason = describe_error(exc)
        row.errors = [*row.errors, {
            "attempt": row.attempts,
            "at": timezone.now().isoformat(),
            "exception_class_path": f"{type(exc).__module__}.{type(exc).__qualname__}",
            "message": reason,
            "traceback": "".join(format_exception(exc)),
        }]
        row.last_error = reason
        if is_final_failure(exc, row.attempts, row.max_attempts):
            why = "ошибка не временная, повтор не поможет" if isinstance(exc, PermanentTaskError) else "попытки исчерпаны"
            logger.error(
                "Задача %s (%s) завершилась сбоем на попытке %d из %d (%s): %s",
                short_id(row.id), _name(row), row.attempts, row.max_attempts, why, reason,
            )
            self._finish_failed(row, reason, timezone.now())
            return
        delay = backoff_delay(row.attempts, self.backend.retry_backoff_seconds, self.backend.retry_backoff_max_seconds)
        row.status = TaskResultStatus.READY
        row.run_after = timezone.now() + timedelta(seconds=delay)
        row.save(update_fields=["status", "run_after", "errors", "last_error"])
        logger.warning(
            "Задача %s (%s) — сбой попытки %d из %d через %.2f с: %s. Повтор через %g с",
            short_id(row.id), _name(row), row.attempts, row.max_attempts, elapsed, reason, delay,
        )

    def _finish_failed(self, row, reason, now):
        row.status = TaskResultStatus.FAILED
        row.finished_at = now
        row.last_error = row.last_error or reason
        row.save(update_fields=["status", "finished_at", "errors", "last_error"])
        # Подписчики (например, платежи — fleet/tasks.py) переводят бизнес-запись в «сбой».
        self._notify_finished(row)

    def _notify_finished(self, row):
        """Сигнал task_finished; ошибка подписчика попадает в лог, но не роняет обработчик."""
        try:
            task_result = to_task_result(row, self.backend.alias)
        except ImportError:
            return  # кода задачи нет — подписчиков на её завершение тоже нет
        responses = task_finished.send_robust(type(self.backend), task_result=task_result)
        for receiver, response in responses:
            if isinstance(response, Exception):
                logger.error(
                    "Обработчик сигнала %s для задачи %s упал: %s",
                    getattr(receiver, "__name__", receiver), short_id(row.id), describe_error(response),
                )

    # --- цикл -----------------------------------------------------------------
    def run_pending(self):
        """Выполнить все задачи, готовые прямо сейчас; вернуть их число."""
        done = 0
        while not self._stopping:
            row = self.claim()
            if row is None:
                break
            self.process(row)
            done += 1
        return done

    def run(self, interval=1.0):
        """Работать, пока не остановят: выполнять готовые задачи, при пустой очереди — ждать."""
        logger.info(
            "Обработчик %s запущен: очереди %s, опрос каждые %g с, попыток на задачу %d, пауза повтора от %g с",
            self.worker_id, ", ".join(self.queues), interval,
            self.backend.max_attempts, self.backend.retry_backoff_seconds,
        )
        try:
            while not self._stopping:
                close_old_connections()  # обрыв соединения с базой не роняет обработчик
                try:
                    done = self.run_pending()
                except DatabaseError as exc:
                    logger.error("База данных недоступна: %s. Повтор через %g с", describe_error(exc), interval * 5)
                    time.sleep(interval * 5)
                    continue
                if not done:
                    time.sleep(interval)
        except KeyboardInterrupt:
            pass
        logger.info("Обработчик %s остановлен", self.worker_id)

    def stop(self):
        self._stopping = True


def _name(row):
    return row.task_path.rsplit(".", 1)[-1]
