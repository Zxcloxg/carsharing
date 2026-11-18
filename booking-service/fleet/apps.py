from django.apps import AppConfig


class FleetConfig(AppConfig):
    """Автопарк — основная сущность предметной области (Car)."""

    name = "fleet"
    verbose_name = "Автопарк"

    def ready(self):
        # Подписка на завершение фоновых задач: сбой списания переводит платёж в failed.
        from . import tasks  # noqa: F401
