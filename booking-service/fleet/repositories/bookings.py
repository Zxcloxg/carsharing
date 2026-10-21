"""Доступ к таблице bookings: создание, чтение, обновление, удаление, поиск по фильтру."""
from ..errors import CarNotAvailable, CustomerHasActiveBooking
from ..models import Booking
from .common import iso, parse_id, save_in_savepoint

FIELDS = ("status", "price_per_minute", "total_cost")


def to_dict(booking):
    return {
        "id": str(booking.id),
        "car_id": str(booking.car_id),
        "customer_id": str(booking.customer_id),
        "customer_phone": booking.customer.phone,
        **{field: getattr(booking, field) for field in FIELDS},
        "started_at": iso(booking.started_at),
        "finished_at": iso(booking.finished_at),
    }


class BookingRepository:
    # --- чтение -----------------------------------------------------------
    def search(self, *, car_id=None, customer_id=None, status=None, offset=0, limit=10):
        query = Booking.objects.select_related("customer")
        if car_id:
            query = query.filter(car_id=car_id)
        if customer_id:
            query = query.filter(customer_id=customer_id)
        if status:
            query = query.filter(status=status)
        total = query.count()
        return [to_dict(b) for b in query.order_by("started_at")[offset:offset + limit]], total

    def get(self, booking_id):
        pk = parse_id(booking_id)
        booking = Booking.objects.select_related("customer").filter(pk=pk).first() if pk else None
        return to_dict(booking) if booking else None

    def get_for_update(self, booking_id):
        pk = parse_id(booking_id)
        query = Booking.objects.select_for_update(of=("self",)).select_related("customer")
        booking = query.filter(pk=pk).first() if pk else None
        return to_dict(booking) if booking else None

    def count(self, **filters):
        return Booking.objects.filter(**filters).count()

    # --- запись -----------------------------------------------------------
    def add(self, *, car_id, customer_id, price_per_minute, started_at):
        booking = Booking(
            car_id=car_id, customer_id=customer_id, price_per_minute=price_per_minute, started_at=started_at
        )
        # Частичные уникальные индексы: активная бронь уже есть у машины или у клиента.
        save_in_savepoint(booking, {
            "bookings_one_active_per_car": lambda: CarNotAvailable(
                details=[{"field": "car_id", "message": f"Car {car_id} already has an active booking"}]
            ),
            "bookings_one_active_per_customer": lambda: CustomerHasActiveBooking(
                details=[{"field": "customer_phone", "message": "Customer already has an active booking"}]
            ),
        })
        return self.get(booking.id)

    def update(self, booking_id, changes):
        booking = Booking.objects.select_related("customer").get(pk=parse_id(booking_id))
        for field, value in changes.items():
            setattr(booking, field, value)
        booking.save()
        return to_dict(booking)

    def delete(self, booking_id):
        Booking.objects.filter(pk=parse_id(booking_id)).delete()
