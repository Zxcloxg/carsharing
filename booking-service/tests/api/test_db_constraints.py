"""Ограничения целостности, которые база PostgreSQL держит сама, даже в обход API (ПР3)."""
from datetime import date, timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone

from fleet.models import Booking, Car, CarModel, Customer, DriverLicense, Payment
from tests.helpers import VALID_CAR, ApiTestCase, make_customer


class DatabaseConstraintTests(ApiTestCase):
    """Ограничения, которые база держит сама — даже в обход API."""

    def setUp(self):
        super().setUp()
        self.car_model = CarModel.objects.create(brand="Toyota", model="Corolla")
        self.car = self._car(plate="777CCC02")
        self.customer = make_customer()
        self.started_at = timezone.now() - timedelta(minutes=5)

    def _car(self, **fields):
        data = {k: v for k, v in VALID_CAR.items() if k not in ("brand", "model")}
        return Car.objects.create(car_model=self.car_model, **{**data, **fields})

    def _booking(self, **fields):
        # Время начала фиксировано заранее: если бы оно бралось здесь через now(), а finished_at — в вызывающем
        # коде, то finished_at мог оказаться на миллисекунду раньше started_at (нестабильный тест).
        data = {"car": self.car, "customer": self.customer, "price_per_minute": 80, "started_at": self.started_at}
        return Booking.objects.create(**{**data, **fields})

    def _assert_rejected(self, create):
        with self.assertRaises(IntegrityError), transaction.atomic():
            create()

    def test_plate_is_unique(self):
        self._assert_rejected(lambda: self._car(plate="777CCC02"))

    def test_price_must_be_positive(self):
        self._assert_rejected(lambda: self._car(plate="778CCC02", price_per_minute=0))

    def test_fuel_level_in_range(self):
        self._assert_rejected(lambda: self._car(plate="779CCC02", fuel_level=1.5))

    def test_car_model_unique_ignoring_case(self):
        self._assert_rejected(lambda: CarModel.objects.create(brand="TOYOTA", model="corolla"))

    def test_customer_phone_format(self):
        self._assert_rejected(
            lambda: Customer.objects.create(phone="call-me", full_name="X", birth_date=date(1990, 1, 1))
        )

    def test_only_one_active_booking_per_car(self):
        self._booking()
        other = make_customer(phone="+77010000002")
        self._assert_rejected(lambda: self._booking(customer=other))

    def test_only_one_active_booking_per_customer(self):
        self._booking()
        self._assert_rejected(lambda: self._booking(car=self._car(plate="780CCC02")))

    def test_finished_booking_requires_finish_time(self):
        self._assert_rejected(lambda: self._booking(status="finished"))

    def test_one_approved_license_per_number(self):
        other = Customer.objects.create(phone="+77010000003", full_name="Другой", birth_date=date(1990, 1, 1))
        number = self.customer.driver_licenses.get().number
        self._assert_rejected(lambda: DriverLicense.objects.create(
            customer=other, number=number, categories="B", issued_on=date(2015, 1, 1),
            expires_on=date(2035, 1, 1), status="approved", reviewed_at=timezone.now(),
        ))

    def test_only_one_paid_payment_per_booking(self):
        booking = self._booking(status="finished", finished_at=self.started_at + timedelta(minutes=1), total_cost=80)
        Payment.objects.create(booking=booking, amount=80, status="paid", paid_at=timezone.now())
        self._assert_rejected(lambda: Payment.objects.create(
            booking=booking, amount=80, status="paid", paid_at=timezone.now()
        ))
