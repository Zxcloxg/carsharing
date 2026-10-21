"""Доступ к таблице payments: запись платежа и платежи брони."""
from ..models import Payment
from .common import iso

FIELDS = ("amount", "status", "provider_payment_id")


def to_dict(payment):
    return {
        "id": str(payment.id),
        "booking_id": str(payment.booking_id),
        **{field: getattr(payment, field) for field in FIELDS},
        "created_at": iso(payment.created_at),
        "paid_at": iso(payment.paid_at),
    }


class PaymentRepository:
    def search(self, *, booking_id=None, status=None):
        query = Payment.objects.all()
        if booking_id:
            query = query.filter(booking_id=booking_id)
        if status:
            query = query.filter(status=status)
        return [to_dict(p) for p in query.order_by("created_at")]

    def count(self, **filters):
        return Payment.objects.filter(**filters).count()

    def add(self, *, booking_id, amount, status, provider_payment_id=None, paid_at=None):
        payment = Payment.objects.create(
            booking_id=booking_id, amount=amount, status=status,
            provider_payment_id=provider_payment_id, paid_at=paid_at,
        )
        return to_dict(payment)
