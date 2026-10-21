"""Учётные записи API в админке: просмотр, смена роли, блокировка.

Создавать пользователей здесь нельзя — только через API или команду
create_user, чтобы пароль всегда проходил валидацию и хэширование.
"""
from django.contrib import admin

from .models import User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("email", "role", "is_active", "customer", "created_at", "last_login_at")
    list_filter = ("role", "is_active")
    search_fields = ("email",)
    fields = ("email", "role", "is_active", "customer", "password_hash", "created_at", "last_login_at")
    readonly_fields = ("email", "password_hash", "created_at", "last_login_at")
    raw_id_fields = ("customer",)

    def has_add_permission(self, request):
        return False
