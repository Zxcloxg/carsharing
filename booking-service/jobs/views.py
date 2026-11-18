"""Маршруты очереди фоновых задач: статус задач для персонала.

Клиенту технические подробности очереди не нужны: результат своей задачи он
видит как статус платежа (GET /api/v1/payments/{id}). Оператор и
администратор видят задачи целиком — сколько попыток, когда следующая и
почему не прошла предыдущая.
"""
from django.tasks import TaskResultStatus
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import STAFF_ROLES, require_role

from . import services


class TaskListQuerySerializer(serializers.Serializer):
    page = serializers.IntegerField(min_value=1, required=False, default=1)
    limit = serializers.IntegerField(min_value=1, max_value=100, required=False, default=10)
    status = serializers.ChoiceField(choices=TaskResultStatus.values, required=False)
    task = serializers.RegexField(r"^[a-z_]{1,50}$", required=False, help_text="Имя задачи, например charge_payment")


class TaskCollectionView(APIView):
    """/api/v1/tasks — очередь: последние задачи, фильтр по статусу и имени."""

    @require_role(*STAFF_ROLES)
    def get(self, request):
        query = TaskListQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        return Response(services.list_tasks(**query.validated_data), status=status.HTTP_200_OK)


class TaskItemView(APIView):
    """/api/v1/tasks/{id} — статус и результат одной задачи."""

    @require_role(*STAFF_ROLES)
    def get(self, request, task_id):
        return Response(services.get_task(task_id), status=status.HTTP_200_OK)
