"""Единый формат ошибок и за пределами маршрутов DRF.

Обработчик DRF (config/exceptions.py) видит только исключения внутри
APIView. Этот middleware закрывает остальное для путей API:

* неизвестный URL под /api/ — Django отдал бы HTML-страницу 404;
* исключение вне APIView — Django отдал бы HTML-стектрейс (при DEBUG)
  или HTML-страницу 500.

В обоих случаях клиент получает JSON в формате {"error": {...}}.
"""
import logging

from django.http import Http404, JsonResponse

from .exceptions import INTERNAL_ERROR_MESSAGE, error_body

logger = logging.getLogger(__name__)

API_PREFIX = "/api/"


class ApiErrorMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        is_json = response.get("Content-Type", "").startswith("application/json")
        if request.path.startswith(API_PREFIX) and response.status_code == 404 and not is_json:
            return JsonResponse(
                error_body("not_found", "Resource not found", [{"field": "path", "message": request.path}]),
                status=404,
            )
        return response

    def process_exception(self, request, exception):
        # Http404 Django сам превратит в ответ 404, а __call__ заменит его на JSON.
        if not request.path.startswith(API_PREFIX) or isinstance(exception, Http404):
            return None
        logger.exception("Unhandled error on %s %s", request.method, request.path, exc_info=exception)
        return JsonResponse(error_body("internal_error", INTERNAL_ERROR_MESSAGE), status=500)
