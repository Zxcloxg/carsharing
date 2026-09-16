from django.apps import AppConfig


class FleetConfig(AppConfig):
    """Автопарк — основная сущность предметной области (Car)."""

    name = "fleet"
    verbose_name = "Автопарк"

    def ready(self):
        """Наполняем хранилище демо-машинами, чтобы API можно было проверить сразу."""
        from .repository import seed_demo_cars

        seed_demo_cars()
