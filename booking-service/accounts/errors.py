"""Ошибки аутентификации, авторизации и управления учётными записями."""
from rest_framework import status

from config.exceptions import DomainError


class InvalidCredentials(DomainError):
    # Одинаковый ответ и для неизвестного email, и для неверного пароля:
    # по ответу нельзя выяснить, зарегистрирован ли адрес.
    status_code = status.HTTP_401_UNAUTHORIZED
    code = "invalid_credentials"
    message = "Invalid email or password"


class AccountDisabled(DomainError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "account_disabled"
    message = "Account is disabled"


class AccessDenied(DomainError):
    """Пользователь аутентифицирован, но прав на действие нет (403, в отличие от 401)."""

    status_code = status.HTTP_403_FORBIDDEN
    code = "permission_denied"
    message = "You do not have permission to perform this action"


class EmailTaken(DomainError):
    status_code = status.HTTP_409_CONFLICT
    code = "email_taken"
    message = "User with this email already exists"


class UserNotFound(DomainError):
    status_code = status.HTTP_404_NOT_FOUND
    code = "user_not_found"
    message = "User not found"


class CannotChangeOwnAccount(DomainError):
    status_code = status.HTTP_409_CONFLICT
    code = "cannot_change_own_account"
    message = "Administrator cannot change own role or disable own account"


class CustomerProfileExists(DomainError):
    status_code = status.HTTP_409_CONFLICT
    code = "customer_profile_exists"
    message = "Account already has a customer profile"


class CustomerProfileRequired(DomainError):
    status_code = status.HTTP_403_FORBIDDEN
    code = "customer_profile_required"
    message = "Create a customer profile first: POST /api/v1/customers"
