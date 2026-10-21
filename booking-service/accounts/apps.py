from django.apps import AppConfig


class AccountsConfig(AppConfig):
    """Учётные записи API: регистрация, вход по JWT, роли."""

    name = "accounts"
    verbose_name = "Учётные записи API"
