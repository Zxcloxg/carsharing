from django.urls import path

from . import views

urlpatterns = [
    path("tasks", views.TaskCollectionView.as_view(), name="task-collection"),
    path("tasks/<str:task_id>", views.TaskItemView.as_view(), name="task-item"),
]
