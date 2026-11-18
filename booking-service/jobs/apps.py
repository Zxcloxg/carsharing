from django.apps import AppConfig


class JobsConfig(AppConfig):
    """Фоновые задачи: очередь в PostgreSQL за стандартным API django.tasks и обработчик run_worker."""

    name = "jobs"
    verbose_name = "Фоновые задачи"
