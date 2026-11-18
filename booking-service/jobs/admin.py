"""Очередь в админке: оператор видит сбойные задачи и может запустить их заново."""
from django.contrib import admin
from django.tasks import TaskResultStatus
from django.utils import timezone

from .models import BackgroundTask


@admin.register(BackgroundTask)
class BackgroundTaskAdmin(admin.ModelAdmin):
    list_display = ("task_path", "status", "attempts", "max_attempts", "enqueued_at", "run_after", "last_error")
    list_filter = ("status", "queue_name", "task_path")
    search_fields = ("id", "task_path", "last_error")
    readonly_fields = [field.name for field in BackgroundTask._meta.fields]
    actions = ["requeue"]

    @admin.action(description="Запустить заново (сбросить попытки)")
    def requeue(self, request, queryset):
        count = queryset.filter(status=TaskResultStatus.FAILED).update(
            status=TaskResultStatus.READY, attempts=0, run_after=timezone.now(), finished_at=None,
        )
        self.message_user(request, f"Поставлено в очередь заново: {count}")
