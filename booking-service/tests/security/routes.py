"""Перечень маршрутов API, собранный из настоящего URLconf, и ожидаемый уровень доступа к каждому.

Тесты безопасности не перечисляют маршруты вручную: они берут их из
config.urls. Новый маршрут, для которого не указан уровень доступа в ACCESS,
сразу роняет тест полноты — так проверка 401/403 не может «забыть» маршрут.
"""
import re

from django.urls import URLPattern, URLResolver, get_resolver

API_PREFIX = "api/v1/"
HTTP_METHODS = ("get", "post", "put", "patch", "delete")
FAKE_ID = "00000000-0000-4000-8000-000000000000"

PUBLIC, AUTH, STAFF, ADMIN = "public", "auth", "staff", "admin"

# Матрица прав из docs/auth.md: кому открыт метод маршрута.
# public — всем без токена; auth — любой вошедший (клиент — к своим записям); staff — operator и admin.
ACCESS = {
    ("POST", "/api/v1/auth/register"): PUBLIC,
    ("POST", "/api/v1/auth/login"): PUBLIC,
    ("GET", "/api/v1/auth/me"): AUTH,
    ("GET", "/api/v1/users"): ADMIN,
    ("GET", "/api/v1/users/{id}"): ADMIN,
    ("PATCH", "/api/v1/users/{id}"): ADMIN,
    ("GET", "/api/v1/cars"): PUBLIC,
    ("POST", "/api/v1/cars"): ADMIN,
    ("GET", "/api/v1/cars/{id}"): PUBLIC,
    ("PUT", "/api/v1/cars/{id}"): ADMIN,
    ("PATCH", "/api/v1/cars/{id}"): STAFF,
    ("DELETE", "/api/v1/cars/{id}"): ADMIN,
    ("GET", "/api/v1/cars/{id}/status-history"): STAFF,
    ("GET", "/api/v1/customers"): STAFF,
    ("POST", "/api/v1/customers"): AUTH,
    ("GET", "/api/v1/customers/{id}"): AUTH,
    ("PATCH", "/api/v1/customers/{id}"): STAFF,
    ("GET", "/api/v1/customers/{id}/driver-licenses"): AUTH,
    ("POST", "/api/v1/customers/{id}/driver-licenses"): AUTH,
    ("GET", "/api/v1/driver-licenses/{id}"): AUTH,
    ("PATCH", "/api/v1/driver-licenses/{id}"): STAFF,
    ("GET", "/api/v1/bookings"): AUTH,
    ("POST", "/api/v1/bookings"): AUTH,
    ("GET", "/api/v1/bookings/{id}"): AUTH,
    ("PATCH", "/api/v1/bookings/{id}"): AUTH,
    ("GET", "/api/v1/bookings/{id}/payments"): AUTH,
}


def _walk(patterns, prefix=""):
    for pattern in patterns:
        if isinstance(pattern, URLResolver):
            yield from _walk(pattern.url_patterns, prefix + str(pattern.pattern))
        elif isinstance(pattern, URLPattern):
            yield prefix + str(pattern.pattern), pattern.callback


def api_routes():
    """{(METHOD, '/api/v1/cars/{id}')} — все методы всех маршрутов API из URLconf проекта."""
    routes = set()
    for route, callback in _walk(get_resolver().url_patterns):
        view_class = getattr(callback, "view_class", None)
        if not route.startswith(API_PREFIX) or view_class is None:
            continue
        template = "/" + re.sub(r"<[^>]+>", "{id}", route)
        routes |= {(method.upper(), template) for method in HTTP_METHODS if hasattr(view_class, method)}
    return routes


def concrete(template):
    return template.replace("{id}", FAKE_ID)
