"""Наблюдаемость (ПР7): структурированные логи, идентификатор запроса, метрики.

* JsonFormatter — каждая запись лога одной строкой JSON: время (UTC), уровень,
  логгер, сообщение, request_id или task_id и дополнительные поля
  (метод, путь, код ответа, длительность). Включается LOG_FORMAT=json —
  так сервис работает в контейнерах; локально по умолчанию читаемый текст.
* RequestContextMiddleware — у каждого запроса свой request_id: берётся из
  заголовка X-Request-ID (его ставит API Gateway) или генерируется, уходит
  в ответ тем же заголовком и попадает во все строки лога этого запроса.
  По завершении — одна строка лога «request» и обновление метрик.
* METRICS — счётчики запросов и гистограмма времени ответа в памяти процесса,
  отдаются в формате Prometheus на GET /metrics (health/views.py).

Модуль не импортирует настройки Django на уровне модуля: форматтер подключает
и конфигурация gunicorn (gunicorn.conf.py), которая читается до Django.
"""
import json
import logging
import re
import threading
import time
import uuid
from contextvars import ContextVar
from datetime import datetime, timezone

request_id_var = ContextVar("request_id", default=None)
task_id_var = ContextVar("task_id", default=None)

REQUEST_ID_HEADER = "X-Request-ID"
_VALID_REQUEST_ID = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
# Атрибуты LogRecord, которые есть у любой записи; всё остальное — поля из extra=...
_STANDARD = set(vars(logging.LogRecord("", 0, "", 0, "", None, None))) | {"message", "asctime", "taskName"}


class ContextFilter(logging.Filter):
    """Подставляет request_id и task_id в запись — их видит и текстовый формат."""

    def filter(self, record):
        record.request_id = getattr(record, "request_id", None) or request_id_var.get()
        record.task_id = getattr(record, "task_id", None) or task_id_var.get()
        return True


class JsonFormatter(logging.Formatter):
    def format(self, record):
        entry = {
            "time": datetime.fromtimestamp(record.created, timezone.utc).isoformat(timespec="milliseconds"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for key in ("request_id", "task_id"):
            value = getattr(record, key, None) or (request_id_var if key == "request_id" else task_id_var).get()
            if value:
                entry[key] = value
        for key, value in vars(record).items():
            if key not in _STANDARD and key not in ("request_id", "task_id") and not key.startswith("_"):
                entry[key] = value
        if record.exc_info:
            entry["exc_info"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """Читаемый формат для локальной разработки; request_id/task_id — в квадратных скобках, если есть."""

    def format(self, record):
        text = super().format(record)
        context = getattr(record, "request_id", None) or getattr(record, "task_id", None)
        return f"{text} [{context}]" if context else text


# --- метрики ------------------------------------------------------------------
DURATION_BUCKETS = (0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0)


class Metrics:
    """Счётчики запросов и гистограмма длительности в памяти процесса (потокобезопасно)."""

    def __init__(self):
        self._lock = threading.Lock()
        self.started = time.time()
        self.reset()

    def reset(self):
        with self._lock:
            self.requests = {}  # (method, route, status) -> count
            self.duration = {}  # route -> [bucket counts..., +Inf, sum, count]
            self.in_flight = 0

    def begin(self):
        with self._lock:
            self.in_flight += 1

    def observe(self, method, route, status, seconds):
        with self._lock:
            self.in_flight -= 1
            key = (method, route, str(status))
            self.requests[key] = self.requests.get(key, 0) + 1
            hist = self.duration.setdefault(route, [0] * (len(DURATION_BUCKETS) + 3))
            for i, bound in enumerate(DURATION_BUCKETS):
                if seconds <= bound:
                    hist[i] += 1
            hist[len(DURATION_BUCKETS)] += 1  # +Inf
            hist[-2] += seconds
            hist[-1] += 1

    def render(self, extra_lines=()):
        """Текстовый формат Prometheus (exposition format 0.0.4)."""
        with self._lock:
            requests = dict(self.requests)
            duration = {route: list(hist) for route, hist in self.duration.items()}
            in_flight = self.in_flight
        lines = [
            "# HELP process_uptime_seconds Время работы процесса API.",
            "# TYPE process_uptime_seconds gauge",
            f"process_uptime_seconds {time.time() - self.started:.1f}",
            "# HELP http_requests_in_flight Запросы, обрабатываемые прямо сейчас.",
            "# TYPE http_requests_in_flight gauge",
            f"http_requests_in_flight {in_flight}",
            "# HELP http_requests_total Обработанные HTTP-запросы по методу, маршруту и коду ответа.",
            "# TYPE http_requests_total counter",
        ]
        for (method, route, status), count in sorted(requests.items()):
            lines.append(f'http_requests_total{{method="{method}",route="{route}",status="{status}"}} {count}')
        lines += [
            "# HELP http_request_duration_seconds Время ответа по маршруту.",
            "# TYPE http_request_duration_seconds histogram",
        ]
        for route, hist in sorted(duration.items()):
            for i, bound in enumerate(DURATION_BUCKETS):
                lines.append(f'http_request_duration_seconds_bucket{{route="{route}",le="{bound}"}} {hist[i]}')
            lines.append(f'http_request_duration_seconds_bucket{{route="{route}",le="+Inf"}} {hist[len(DURATION_BUCKETS)]}')
            lines.append(f'http_request_duration_seconds_sum{{route="{route}"}} {hist[-2]:.6f}')
            lines.append(f'http_request_duration_seconds_count{{route="{route}"}} {hist[-1]}')
        lines.extend(extra_lines)
        return "\n".join(lines) + "\n"


METRICS = Metrics()
access_logger = logging.getLogger("config.requests")
QUIET_PATHS = ("/health", "/health/", "/health/live", "/metrics")


def route_of(request):
    """Шаблон маршрута вместо конкретного пути: /api/v1/cars/{car_id}, а не тысячи разных id."""
    match = getattr(request, "resolver_match", None)
    if match is None or not match.route:
        return "unmatched"
    return "/" + re.sub(r"<(?:\w+:)?(\w+)>", r"{\1}", match.route)


class RequestContextMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        incoming = request.headers.get(REQUEST_ID_HEADER, "")
        request_id = incoming if _VALID_REQUEST_ID.match(incoming) else uuid.uuid4().hex
        request.request_id = request_id
        token = request_id_var.set(request_id)
        METRICS.begin()
        started = time.perf_counter()
        status = 500
        try:
            response = self.get_response(request)
            status = response.status_code
            response[REQUEST_ID_HEADER] = request_id
            return response
        finally:
            elapsed = time.perf_counter() - started
            route = route_of(request)
            METRICS.observe(request.method, route, status, elapsed)
            # Проверки здоровья и сбор метрик идут каждые несколько секунд — в лог только на уровне DEBUG.
            level = logging.DEBUG if request.path in QUIET_PATHS and status < 500 else logging.INFO
            access_logger.log(
                level, "%s %s -> %s за %.1f мс", request.method, request.path, status, elapsed * 1000,
                extra={"request_id": request_id, "method": request.method, "path": request.path, "route": route, "status": status,
                       "duration_ms": round(elapsed * 1000, 1)},
            )
            request_id_var.reset(token)
