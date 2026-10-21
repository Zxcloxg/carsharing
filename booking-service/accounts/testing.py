"""Помощники автотестов: учётные записи нужных ролей и вход без лишних запросов к API."""
from .models import Role
from .repository import UserRepository
from .tokens import issue_access_token

TEST_PASSWORD = "Str0ng#Passw0rd"

users = UserRepository()


def make_account(role=Role.CUSTOMER, email=None, customer_id=None, password=TEST_PASSWORD):
    email = email or f"{role}-{users.search()[1] + 1}@test.kz"
    user = users.add(email=email, raw_password=password, role=role)
    if customer_id:
        user = users.update(user["id"], {"customer_id": str(customer_id)})
    return user


def authenticate(client, user):
    """Подставляет в тестовый клиент заголовок Authorization: Bearer <token>."""
    client.credentials(HTTP_AUTHORIZATION=f"Bearer {issue_access_token(user)}")
    return user
