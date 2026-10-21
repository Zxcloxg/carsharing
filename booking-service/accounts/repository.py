"""Доступ к таблице users. Единственное место приложения accounts с обращениями к ORM.

Наружу отдаются словари. Хэш пароля попадает в словарь только из
find_credentials — для проверки при входе; остальные методы его не отдают.
"""
from django.db import IntegrityError, transaction

from fleet.repositories.common import iso, parse_id

from .errors import EmailTaken
from .models import User


def to_dict(user):
    return {
        "id": str(user.id),
        "email": user.email,
        "role": user.role,
        "customer_id": str(user.customer_id) if user.customer_id else None,
        "is_active": user.is_active,
        "created_at": iso(user.created_at),
        "last_login_at": iso(user.last_login_at),
    }


class UserRepository:
    # --- чтение -----------------------------------------------------------
    def get(self, user_id):
        pk = parse_id(user_id)
        user = User.objects.filter(pk=pk).first() if pk else None
        return to_dict(user) if user else None

    def get_for_update(self, user_id):
        user = User.objects.select_for_update().filter(pk=parse_id(user_id)).first()
        return to_dict(user) if user else None

    def find_credentials(self, email):
        user = User.objects.filter(email__iexact=email).first()
        return {**to_dict(user), "password_hash": user.password_hash} if user else None

    def search(self, *, role=None, offset=0, limit=10):
        query = User.objects.all()
        if role:
            query = query.filter(role=role)
        total = query.count()
        return [to_dict(u) for u in query.order_by("created_at")[offset:offset + limit]], total

    # --- запись -----------------------------------------------------------
    def add(self, *, email, raw_password, role, customer_id=None):
        user = User(email=email.lower(), role=role, customer_id=customer_id)
        user.set_password(raw_password)
        try:
            with transaction.atomic():
                user.save()
        except IntegrityError as exc:
            if "users_email_lower_uniq" in str(exc):
                raise EmailTaken(
                    details=[{"field": "email", "message": f"Email {user.email} is already registered"}]
                ) from exc
            raise
        return to_dict(user)

    def set_password(self, user_id, raw_password):
        user = User.objects.get(pk=parse_id(user_id))
        user.set_password(raw_password)
        user.save(update_fields=["password_hash"])

    def update(self, user_id, changes):
        User.objects.filter(pk=parse_id(user_id)).update(**changes)
        return self.get(user_id)
