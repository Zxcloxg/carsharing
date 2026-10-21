"""Доступ к таблице car_status_history: запись смены статуса машины и её история."""
from ..models import CarStatusChange
from .common import iso


def to_dict(change):
    return {
        "old_status": change.old_status,
        "new_status": change.new_status,
        "booking_id": str(change.booking_id) if change.booking_id else None,
        "changed_at": iso(change.changed_at),
    }


class CarStatusHistoryRepository:
    def search(self, *, car_id):
        return [to_dict(c) for c in CarStatusChange.objects.filter(car_id=car_id).order_by("changed_at", "id")]

    def count(self, **filters):
        return CarStatusChange.objects.filter(**filters).count()

    def add(self, *, car_id, old_status, new_status, booking_id=None):
        return to_dict(CarStatusChange.objects.create(
            car_id=car_id, old_status=old_status, new_status=new_status, booking_id=booking_id
        ))
