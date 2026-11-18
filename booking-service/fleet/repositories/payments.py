"""Доступ к таблице payments: запись платежа, смена статуса и платежи брони."""
from ..models import Payment
from .common import iso, parse_id

FIELDS = ("amount", "status", "provider_payment_id", "failure_reason")


def to_dict(payment):
    return {
        "id": str(payment.id),
        "booking_id": str(payment.booking_id),
        **{field: getattr(payment, field) for field in FIELDS},
        "task_id": str(payment.task_id) if payment.task_id else None,
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

    def get(self, payment_id):
        pk = parse_id(payment_id)
        payment = Payment.objects.filter(pk=pk).first() if pk else None
        return to_dict(payment) if payment else None

    def get_for_update(self, payment_id):
        pk = parse_id(payment_id)
        payment = Payment.objects.select_for_update().filter(pk=pk).first() if pk else None
        return to_dict(payment) if payment else None

    def count(self, **filters):
        return Payment.objects.filter(**filters).count()

    def add(self, *, booking_id, amount, status, provider_payment_id=None, paid_at=None):
        payment = Payment.objects.create(
            booking_id=booking_id, amount=amount, status=status,
            provider_payment_id=provider_payment_id, paid_at=paid_at,
        )
        return to_dict(payment)

    def update(self, payment_id, changes):
        payment = Payment.objects.get(pk=parse_id(payment_id))
        for field, value in changes.items():
            setattr(payment, field, value)
        payment.save(update_fields=list(changes))
        return to_dict(payment)
