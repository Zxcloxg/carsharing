"""Коды 401/403 на всех защищённых маршрутах (OWASP API2:2023, API5:2023).

Маршруты берутся из URLconf (tests/security/routes.py), поэтому проверка
полная по построению: каждый метод каждого маршрута API вызывается от имени
анонима, клиента, оператора и администратора.
"""
from accounts.models import Role
from tests.helpers import ApiTestCase, authenticate, make_account, make_customer

from .routes import ACCESS, ADMIN, AUTH, PUBLIC, STAFF, api_routes, concrete

DENIED_FOR = {
    Role.CUSTOMER: {STAFF, ADMIN},
    Role.OPERATOR: {ADMIN},
    Role.ADMIN: set(),
}


class RouteProtectionTests(ApiTestCase):
    def _call(self, method, template):
        """Запрос к маршруту с несуществующим id; у изменяющих методов — пустое JSON-тело."""
        data = "{}" if method in ("POST", "PUT", "PATCH") else ""
        return self.client.generic(method, concrete(template), data=data, content_type="application/json")

    def test_every_route_has_declared_access_level(self):
        """Матрица прав покрывает все маршруты, и в ней нет маршрутов, которых уже нет в коде."""
        self.assertEqual(api_routes(), set(ACCESS))

    def test_anonymous_gets_401_on_every_protected_route(self):
        for (method, template), level in sorted(ACCESS.items()):
            with self.subTest(route=f"{method} {template}"):
                response = self._call(method, template)
                if level == PUBLIC:
                    self.assertNotIn(response.status_code, (401, 403))
                else:
                    self.assertError(response, 401, "not_authenticated")
                    self.assertEqual(response["WWW-Authenticate"], 'Bearer realm="api"')

    def test_roles_get_403_on_routes_above_their_level(self):
        for role, denied_levels in DENIED_FOR.items():
            # У клиента есть профиль арендатора: иначе часть маршрутов ответит 403 customer_profile_required.
            profile = make_customer().id if role == Role.CUSTOMER else None
            authenticate(self.client, make_account(role, customer_id=profile))
            for (method, template), level in sorted(ACCESS.items()):
                with self.subTest(role=role, route=f"{method} {template}"):
                    response = self._call(method, template)
                    if level in denied_levels:
                        self.assertError(response, 403, "permission_denied")
                    else:
                        # Права есть: дальше 404 (записи нет) или 400 (пустое тело), но не 401/403.
                        self.assertNotIn(response.status_code, (401, 403), f"{role}: {response.content}")

    def test_invalid_token_gets_401_even_on_public_route(self):
        """Испорченный токен — всегда 401: клиент должен узнать, что токен больше не работает."""
        self.client.credentials(HTTP_AUTHORIZATION="Bearer broken.token.value")
        for (method, template), level in sorted(ACCESS.items()):
            with self.subTest(route=f"{method} {template}"):
                self.assertError(self._call(method, template), 401, "invalid_token")

    def test_public_auth_routes_accept_request_with_token(self):
        """До исправления DRF считал лимит входа по user.pk, которого у CurrentUser не было, — ответ 500."""
        authenticate(self.client, make_account(Role.CUSTOMER))
        response = self.client.post("/api/v1/auth/login", {"email": "x@test.kz", "password": "Wrong#Pass0"},
                                    format="json")
        self.assertError(response, 401, "invalid_credentials")
        self.assertError(self.client.post("/api/v1/auth/register", {}, format="json"), 400, "validation_error")

    def test_access_levels_are_known(self):
        self.assertLessEqual(set(ACCESS.values()), {PUBLIC, AUTH, STAFF, ADMIN})
