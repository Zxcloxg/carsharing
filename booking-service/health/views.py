"""Проверка состояния и метрики (ПР7).

* GET /health — готовность (readiness): сервис жив И база данных отвечает.
  200 {"status": "ok", ...} или 503 {"status": "unavailable", ...}. По этому
  эндпоинту docker compose ставит контейнеру статус healthy/unhealthy, а
  балансировщик решает, слать ли на экземпляр трафик. Заодно — состояние
  очереди фоновых задач (ПР6): сколько задач ждёт и сколько упало.
* GET /health/live — живость (liveness): процесс отвечает, без проверки
  зависимостей. Перезапускать контейнер из-за упавшей базы бессмысленно,
  поэтому оркестратор перезапускает только по этому эндпоинту.
* GET /metrics — метрики в формате Prometheus. Если задан METRICS_TOKEN,
  нужен заголовок Authorization: Bearer <METRICS_TOKEN>.

Причина недоступности базы пишется в лог полностью, а наружу уходит только
класс ошибки: имена хостов и параметры подключения публичному эндпоинту ни к чему.
"""
import hmac
import logging
import time

from django.conf import settings
from django.db import DatabaseError, connection
from django.db.models import Count, Min
from django.http import HttpResponse, JsonResponse
from django.tasks import TaskResultStatus
from django.utils import timezone

from config.observability import METRICS

logger = logging.getLogger("health")
SERVICE = {"service": "booking-service", "framework": "django"}


def _check_database():
    started = time.perf_counter()
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except DatabaseError as exc:
        logger.error("Проверка здоровья: база данных недоступна: %s: %s", type(exc).__name__, str(exc).strip())
        connection.close()  # сломанное соединение не переиспользуем: следующий запрос подключится заново
        return {"status": "error", "error": f"{type(exc).__name__}: database is unreachable"}
    return {"status": "ok", "latency_ms": round((time.perf_counter() - started) * 1000, 1)}


def _queue_state():
    from jobs.models import BackgroundTask

    counts = dict(BackgroundTask.objects.values_list("status").annotate(n=Count("id")).values_list("status", "n"))
    oldest = BackgroundTask.objects.filter(
        status=TaskResultStatus.READY, run_after__lte=timezone.now()
    ).aggregate(oldest=Min("run_after"))["oldest"]
    return {
        "status": "ok",
        "ready": counts.get(TaskResultStatus.READY, 0),
        "running": counts.get(TaskResultStatus.RUNNING, 0),
        "failed": counts.get(TaskResultStatus.FAILED, 0),
        # Давно ждущая готовая задача — признак того, что обработчик (run_worker) не работает.
        "oldest_ready_age_s": round((timezone.now() - oldest).total_seconds(), 1) if oldest else 0,
    }


def health(request):
    database = _check_database()
    checks = {"database": database}
    if database["status"] == "ok":
        checks["queue"] = _queue_state()
    healthy = database["status"] == "ok"
    return JsonResponse(
        {
            "status": "ok" if healthy else "unavailable",
            **SERVICE,
            "time": timezone.now().isoformat(),
            "uptime_s": round(time.time() - METRICS.started, 1),
            "checks": checks,
        },
        status=200 if healthy else 503,
    )


def live(request):
    return JsonResponse({"status": "ok", **SERVICE, "time": timezone.now().isoformat()})


def _authorized(request):
    token = settings.METRICS_TOKEN
    if not token:
        return True
    given = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
    return hmac.compare_digest(given.encode(), token.encode())


def metrics(request):
    if not _authorized(request):
        return HttpResponse("Unauthorized\n", status=401, content_type="text/plain; charset=utf-8",
                            headers={"WWW-Authenticate": 'Bearer realm="metrics"'})
    extra = []
    try:
        from fleet.models import Payment
        from jobs.models import BackgroundTask

        extra.append("# HELP background_tasks Задачи очереди по статусу.")
        extra.append("# TYPE background_tasks gauge")
        tasks = dict(BackgroundTask.objects.values_list("status").annotate(n=Count("id")).values_list("status", "n"))
        for status in TaskResultStatus.values:
            extra.append(f'background_tasks{{status="{status}"}} {tasks.get(status, 0)}')
        extra.append("# HELP payments Платежи по статусу.")
        extra.append("# TYPE payments gauge")
        payments = dict(Payment.objects.values_list("status").annotate(n=Count("id")).values_list("status", "n"))
        for status in ("pending", "paid", "failed", "refunded"):
            extra.append(f'payments{{status="{status}"}} {payments.get(status, 0)}')
        extra += ["# HELP database_up База данных отвечает.", "# TYPE database_up gauge", "database_up 1"]
    except DatabaseError:
        connection.close()
        extra = ["# HELP database_up База данных отвечает.", "# TYPE database_up gauge", "database_up 0"]
    return HttpResponse(METRICS.render(extra), content_type="text/plain; version=0.0.4; charset=utf-8")
