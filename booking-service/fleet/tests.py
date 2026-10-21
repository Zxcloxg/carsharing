"""Автотесты REST API каршеринга поверх PostgreSQL.

CRUD машин, валидация, пагинация и фильтрация (ПР2), а также клиенты и
верификация ВУ, брони и оплата, ограничения целостности базы и откат
транзакций (ПР3). Django создаёт для тестов отдельную базу test_<DB_NAME>
и прогоняет на ней все миграции.

С ПР4 изменение данных требует токена, поэтому эти сценарии выполняются от
имени администратора (StaffApiTestCase). Разграничение прав по ролям и
владельцу записи проверяют тесты в accounts/tests.py.
"""
from datetime import date, timedelta
from unittest import mock

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from accounts.models import Role
from accounts.testing import authenticate, make_account

from .models import Booking, Car, CarModel, CarStatusChange, Customer, DriverLicense, Payment
from .repositories import car_repository, car_status_history_repository

VALID_CUSTOMER = {
    "phone": "+77011234567",
    "full_name": "Айгерим Сапарова",
    "birth_date": "1995-04-12",
    "email": "aigerim@example.kz",
}

VALID_LICENSE = {
    "number": "KZ0123456",
    "categories": "B",
    "issued_on": "2018-06-01",
    "expires_on": "2028-06-01",
}

VALID_CAR = {
    "plate": "001AAA02",
    "brand": "Toyota",
    "model": "Corolla",
    "year": 2024,
    "color": "black",
    "price_per_minute": 80,
    "fuel_level": 0.9,
}


def make_customer(phone="+77011234567", approved=True):
    """Клиент с подтверждённым (или ожидающим проверки) удостоверением — в обход API."""
    customer = Customer.objects.create(phone=phone, full_name="Тестовый Клиент", birth_date=date(1990, 1, 1))
    DriverLicense.objects.create(
        customer=customer, number=f"KZ{phone[-7:]}", categories="B",
        issued_on=date(2015, 1, 1), expires_on=date(2035, 1, 1),
        status="approved" if approved else "pending", reviewed_at=timezone.now() if approved else None,
    )
    return customer


class StaffApiTestCase(APITestCase):
    """Запросы от имени администратора — роли с полным набором прав."""

    def setUp(self):
        super().setUp()
        authenticate(self.client, make_account(Role.ADMIN))


class CarApiTests(StaffApiTestCase):
    def _create(self, **overrides):
        response = self.client.post("/api/v1/cars", {**VALID_CAR, **overrides}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        return response.data

    # --- CRUD -------------------------------------------------------------
    def test_create_returns_201_with_generated_id(self):
        car = self._create()
        self.assertIn("id", car)
        self.assertEqual(car["status"], "free")

    def test_get_existing_car(self):
        car = self._create()
        response = self.client.get(f"/api/v1/cars/{car['id']}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["plate"], VALID_CAR["plate"])

    def test_get_unknown_car_returns_404_in_error_format(self):
        response = self.client.get("/api/v1/cars/does-not-exist")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["error"]["message"], "Car not found")
        self.assertTrue(response.data["error"]["details"])

    def test_put_replaces_car(self):
        car = self._create()
        payload = {**VALID_CAR, "brand": "Kia", "model": "Rio", "price_per_minute": 55}
        response = self.client.put(f"/api/v1/cars/{car['id']}", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["brand"], "Kia")
        self.assertEqual(response.data["created_at"], car["created_at"])

    def test_patch_updates_single_field(self):
        car = self._create()
        response = self.client.patch(f"/api/v1/cars/{car['id']}", {"status": "service"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "service")
        self.assertEqual(response.data["brand"], VALID_CAR["brand"])

    def test_delete_returns_204_and_then_404(self):
        car = self._create()
        self.assertEqual(
            self.client.delete(f"/api/v1/cars/{car['id']}").status_code,
            status.HTTP_204_NO_CONTENT,
        )
        self.assertEqual(
            self.client.get(f"/api/v1/cars/{car['id']}").status_code,
            status.HTTP_404_NOT_FOUND,
        )

    # --- валидация --------------------------------------------------------
    def test_missing_required_field_returns_400_not_500(self):
        payload = {k: v for k, v in VALID_CAR.items() if k != "brand"}
        response = self.client.post("/api/v1/cars", payload, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"]["message"], "Validation failed")
        self.assertEqual([d["field"] for d in response.data["error"]["details"]], ["brand"])

    def test_wrong_type_returns_400(self):
        response = self.client.post("/api/v1/cars", {**VALID_CAR, "year": "недавно"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual([d["field"] for d in response.data["error"]["details"]], ["year"])

    def test_non_positive_price_returns_400(self):
        response = self.client.post("/api/v1/cars", {**VALID_CAR, "price_per_minute": 0}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_plate_returns_409(self):
        self._create()
        response = self.client.post("/api/v1/cars", VALID_CAR, format="json")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["error"]["message"], "Car with this plate already exists")

    # --- справочник моделей и журнал статусов -----------------------------
    def test_brand_and_model_go_to_reference_table(self):
        self._create(plate="111AAA02", brand="Kia", model="Rio")
        self._create(plate="222AAA02", brand="KIA", model="rio")
        self.assertEqual(CarModel.objects.count(), 1)  # без учёта регистра — одна запись справочника

    def test_status_changes_are_logged(self):
        car = self._create()
        self.client.patch(f"/api/v1/cars/{car['id']}", {"status": "service"}, format="json")
        response = self.client.get(f"/api/v1/cars/{car['id']}/status-history")
        self.assertEqual(
            [(i["old_status"], i["new_status"]) for i in response.data["items"]],
            [(None, "free"), ("free", "service")],
        )

    # --- пагинация и фильтрация ------------------------------------------
    def test_list_is_paginated(self):
        for i in range(12):
            self._create(plate=f"0{i:02d}BBB02")
        response = self.client.get("/api/v1/cars?page=2&limit=5")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(len(response.data["items"]), 5)
        self.assertEqual(response.data["total"], 12)
        self.assertEqual(response.data["pages"], 3)

    def test_list_filters_by_status_and_brand(self):
        self._create(plate="111AAA02", brand="Kia", status="service")
        self._create(plate="222AAA02", brand="Toyota")
        response = self.client.get("/api/v1/cars?status=service")
        self.assertEqual(response.data["total"], 1)
        self.assertEqual(response.data["items"][0]["brand"], "Kia")

        response = self.client.get("/api/v1/cars?brand=toyo")
        self.assertEqual(response.data["total"], 1)

    def test_invalid_query_param_returns_400(self):
        response = self.client.get("/api/v1/cars?status=flying")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class CustomerApiTests(StaffApiTestCase):
    def _register(self, **overrides):
        return self.client.post("/api/v1/customers", {**VALID_CUSTOMER, **overrides}, format="json")

    def _submit(self, customer_id, **overrides):
        return self.client.post(
            f"/api/v1/customers/{customer_id}/driver-licenses", {**VALID_LICENSE, **overrides}, format="json"
        )

    def test_register_customer(self):
        response = self._register()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "active")
        self.assertFalse(response.data["has_valid_license"])

    def test_duplicate_phone_returns_409(self):
        self._register()
        response = self._register(email="other@example.kz")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["error"]["message"], "Customer with this phone already exists")

    def test_underage_customer_returns_400(self):
        response = self._register(birth_date=str(date.today() - timedelta(days=365 * 17)))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(response.data["error"]["details"][0]["field"], "birth_date")

    def test_license_approval_opens_rent(self):
        customer = self._register().data
        license_ = self._submit(customer["id"]).data
        self.assertEqual(license_["status"], "pending")

        response = self.client.patch(f"/api/v1/driver-licenses/{license_['id']}", {"status": "approved"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIsNotNone(response.data["reviewed_at"])
        self.assertTrue(self.client.get(f"/api/v1/customers/{customer['id']}").data["has_valid_license"])

    def test_second_live_license_returns_409(self):
        customer = self._register().data
        self._submit(customer["id"])
        response = self._submit(customer["id"], number="KZ7654321")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_rejected_license_can_be_resubmitted(self):
        customer = self._register().data
        license_ = self._submit(customer["id"]).data
        self.client.patch(
            f"/api/v1/driver-licenses/{license_['id']}",
            {"status": "rejected", "reject_reason": "Фото нечитаемо"}, format="json",
        )
        self.assertEqual(self._submit(customer["id"]).status_code, status.HTTP_201_CREATED)

    def test_reject_without_reason_returns_400(self):
        customer = self._register().data
        license_ = self._submit(customer["id"]).data
        response = self.client.patch(f"/api/v1/driver-licenses/{license_['id']}", {"status": "rejected"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_license_without_category_b_returns_400(self):
        customer = self._register().data
        self.assertEqual(self._submit(customer["id"], categories="A").status_code, status.HTTP_400_BAD_REQUEST)


class BookingApiTests(StaffApiTestCase):
    def setUp(self):
        super().setUp()
        self.car = self.client.post("/api/v1/cars", VALID_CAR, format="json").data
        self.customer = make_customer()

    def _book(self, car_id=None, phone="+77011234567"):
        return self.client.post(
            "/api/v1/bookings", {"car_id": car_id or self.car["id"], "customer_phone": phone}, format="json"
        )

    def _car_status(self):
        return self.client.get(f"/api/v1/cars/{self.car['id']}").data["status"]

    def test_booking_takes_car_and_fixes_price(self):
        response = self._book()
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["status"], "active")
        self.assertEqual(response.data["customer_id"], str(self.customer.id))
        self.assertEqual(float(response.data["price_per_minute"]), VALID_CAR["price_per_minute"])
        self.assertEqual(self._car_status(), "booked")

    def test_second_booking_of_same_car_returns_409(self):
        self._book()
        make_customer(phone="+77019999999")
        response = self._book(phone="+77019999999")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(Booking.objects.count(), 1)

    def test_customer_cannot_book_two_cars(self):
        self._book()
        other = self.client.post("/api/v1/cars", {**VALID_CAR, "plate": "002AAA02"}, format="json").data
        response = self._book(car_id=other["id"])
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["error"]["message"], "Customer already has an active booking")

    def test_unverified_customer_gets_403(self):
        make_customer(phone="+77015555555", approved=False)
        response = self._book(phone="+77015555555")
        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
        self.assertEqual(self._car_status(), "free")

    def test_unknown_customer_returns_404(self):
        response = self._book(phone="+77000000000")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["error"]["message"], "Customer not found")

    def test_booking_unknown_car_returns_404(self):
        response = self._book(car_id="00000000-0000-0000-0000-000000000000")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)

    def test_finish_booking_frees_car_counts_cost_and_pays(self):
        booking = self._book().data
        response = self.client.patch(f"/api/v1/bookings/{booking['id']}", {"status": "finished"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "finished")
        self.assertIsNotNone(response.data["finished_at"])
        self.assertEqual(float(response.data["total_cost"]), VALID_CAR["price_per_minute"])  # минимум 1 минута
        self.assertEqual(self._car_status(), "free")

        payments = self.client.get(f"/api/v1/bookings/{booking['id']}/payments").data["items"]
        self.assertEqual(
            [(p["status"], float(p["amount"])) for p in payments], [("paid", VALID_CAR["price_per_minute"])]
        )

    def test_cancelled_booking_has_no_payment(self):
        booking = self._book().data
        self.client.patch(f"/api/v1/bookings/{booking['id']}", {"status": "cancelled"}, format="json")
        self.assertEqual(Payment.objects.count(), 0)

    def test_closed_booking_cannot_be_closed_again(self):
        booking = self._book().data
        self.client.patch(f"/api/v1/bookings/{booking['id']}", {"status": "cancelled"}, format="json")
        response = self.client.patch(f"/api/v1/bookings/{booking['id']}", {"status": "finished"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

    def test_car_with_bookings_cannot_be_deleted(self):
        self._book()
        response = self.client.delete(f"/api/v1/cars/{self.car['id']}")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertTrue(Car.objects.filter(pk=self.car["id"]).exists())

    def test_list_bookings_filtered_by_car_and_customer(self):
        self._book()
        self.assertEqual(self.client.get(f"/api/v1/bookings?car_id={self.car['id']}").data["total"], 1)
        self.assertEqual(self.client.get(f"/api/v1/bookings?customer_id={self.customer.id}").data["total"], 1)

    # --- транзакции ---------------------------------------------------------
    def _assert_internal_error(self, response):
        """Сбой отдан клиенту как 500 в едином формате, без текста исключения и стектрейса."""
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertEqual(response.json(), {
            "error": {"code": "internal_error", "message": "Internal server error", "details": []},
        })

    def test_failure_on_last_step_rolls_back_whole_booking(self):
        """Бронь уже записана, но смена статуса машины падает — откатывается всё."""
        with mock.patch.object(car_repository, "update", side_effect=RuntimeError("boom")),                 self.assertLogs("config.exceptions", "ERROR"):
            self._assert_internal_error(self._book())
        self.assertEqual(Booking.objects.count(), 0)
        self.assertEqual(Car.objects.get(pk=self.car["id"]).status, "free")

    def test_failure_on_last_step_rolls_back_whole_finish(self):
        """Бронь закрыта и платёж записан, но запись в журнал статусов падает — откатывается всё."""
        booking = self._book().data
        history_before = CarStatusChange.objects.count()
        with mock.patch.object(car_status_history_repository, "add", side_effect=RuntimeError("boom")),                 self.assertLogs("config.exceptions", "ERROR"):
            self._assert_internal_error(
                self.client.patch(f"/api/v1/bookings/{booking['id']}", {"status": "finished"}, format="json")
            )
        self.assertEqual(Booking.objects.get(pk=booking["id"]).status, "active")
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(Car.objects.get(pk=self.car["id"]).status, "booked")
        self.assertEqual(CarStatusChange.objects.count(), history_before)


class DatabaseConstraintTests(APITestCase):
    """Ограничения, которые база держит сама — даже в обход API."""

    def setUp(self):
        self.car_model = CarModel.objects.create(brand="Toyota", model="Corolla")
        self.car = self._car(plate="777CCC02")
        self.customer = make_customer()

    def _car(self, **fields):
        data = {k: v for k, v in VALID_CAR.items() if k not in ("brand", "model")}
        return Car.objects.create(car_model=self.car_model, **{**data, **fields})

    def _booking(self, **fields):
        data = {"car": self.car, "customer": self.customer, "price_per_minute": 80, "started_at": timezone.now()}
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
        booking = self._booking(status="finished", finished_at=timezone.now(), total_cost=80)
        Payment.objects.create(booking=booking, amount=80, status="paid", paid_at=timezone.now())
        self._assert_rejected(lambda: Payment.objects.create(
            booking=booking, amount=80, status="paid", paid_at=timezone.now()
        ))
