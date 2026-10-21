"""Создание учётной записи API с любой ролью — так заводится первый администратор.

    python manage.py create_user admin@carsharing.kz --role admin

Пароль запрашивается интерактивно (или передаётся --password для скриптов)
и проходит те же проверки, что и при регистрации через API.
"""
from getpass import getpass

from django.core.management.base import BaseCommand, CommandError

from accounts import services
from accounts.models import Role
from accounts.serializers import RegisterSerializer
from config.exceptions import DomainError


class Command(BaseCommand):
    help = "Create an API user with the given role"

    def add_arguments(self, parser):
        parser.add_argument("email")
        parser.add_argument("--role", choices=Role.values, default=Role.CUSTOMER)
        parser.add_argument("--password", help="Password (prompted if omitted)")

    def handle(self, *args, email, role, password, **options):
        if password is None:
            password = getpass("Password: ")
            if password != getpass("Password (again): "):
                raise CommandError("Passwords do not match")

        body = RegisterSerializer(data={"email": email, "password": password})
        if not body.is_valid():
            raise CommandError("; ".join(
                f"{field}: {message}" for field, messages in body.errors.items() for message in messages
            ))
        try:
            user = services.create_user(**body.validated_data, role=role)
        except DomainError as exc:
            raise CommandError(exc.message) from exc
        self.stdout.write(self.style.SUCCESS(f"Created {user['role']} {user['email']} (id {user['id']})"))
