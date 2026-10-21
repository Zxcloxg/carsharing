from django.urls import path

from . import views

urlpatterns = [
    path("auth/register", views.RegisterView.as_view(), name="auth-register"),
    path("auth/login", views.LoginView.as_view(), name="auth-login"),
    path("auth/me", views.MeView.as_view(), name="auth-me"),
    path("users", views.UserCollectionView.as_view(), name="user-collection"),
    path("users/<str:user_id>", views.UserItemView.as_view(), name="user-item"),
]
