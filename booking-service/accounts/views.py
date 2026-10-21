"""Маршруты аутентификации и управления пользователями."""
from rest_framework import status
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView

from . import services
from .models import Role
from .permissions import require_auth, require_role
from .serializers import LoginSerializer, RegisterSerializer, UserListQuerySerializer, UserPatchSerializer


class RegisterView(APIView):
    """/api/v1/auth/register — регистрация клиента."""

    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "register"

    def post(self, request):
        body = RegisterSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        user = services.register(**body.validated_data)
        return Response(user, status=status.HTTP_201_CREATED, headers={"Location": f"/api/v1/users/{user['id']}"})


class LoginView(APIView):
    """/api/v1/auth/login — обмен email и пароля на access-токен."""

    # Ограничение частоты попыток с одного IP — защита от перебора паролей.
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "login"

    def post(self, request):
        body = LoginSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return Response(services.login(**body.validated_data), status=status.HTTP_200_OK)


class MeView(APIView):
    """/api/v1/auth/me — кто я: учётная запись по текущему токену."""

    @require_auth
    def get(self, request):
        return Response(services.get_user(request.user.id), status=status.HTTP_200_OK)


class UserCollectionView(APIView):
    """/api/v1/users — список учётных записей (только администратор)."""

    @require_role(Role.ADMIN)
    def get(self, request):
        query = UserListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response(services.list_users(**query.validated_data), status=status.HTTP_200_OK)


class UserItemView(APIView):
    """/api/v1/users/{id} — просмотр, смена роли и блокировка (только администратор)."""

    @require_role(Role.ADMIN)
    def get(self, request, user_id):
        return Response(services.get_user(user_id), status=status.HTTP_200_OK)

    @require_role(Role.ADMIN)
    def patch(self, request, user_id):
        body = UserPatchSerializer(data=request.data)
        body.is_valid(raise_exception=True)
        return Response(services.update_user(request.user, user_id, body.validated_data), status=status.HTTP_200_OK)
