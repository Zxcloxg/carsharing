"""API-тесты бронирования и оплаты, включая откат транзакций при сбое (ПР3)."""
from unittest import mock

from rest_framework import status

from fleet.models import Booking, Car, CarStatusChange, Payment
from fleet.repositories import car_repository, car_status_history_repository
from jobs.models import BackgroundTask
from tests.helpers import VALID_CAR, StaffApiTestCase, make_customer


class BookingApiTests(StaffApiTestCase):
    """Бронирование и оплата: занятие машины, стоимость, платёж, конфликты и откат транзакций."""

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

    def test_get_booking(self):
        booking = self._book().data
        response = self.client.get(f"/api/v1/bookings/{booking['id']}")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data, booking)

    def test_get_unknown_booking_returns_404(self):
        response = self.client.get("/api/v1/bookings/00000000-0000-4000-8000-000000000000")
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["error"]["code"], "booking_not_found")

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

    def test_finish_booking_frees_car_counts_cost_and_queues_payment(self):
        booking = self._book().data
        response = self.client.patch(f"/api/v1/bookings/{booking['id']}", {"status": "finished"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "finished")
        self.assertIsNotNone(response.data["finished_at"])
        self.assertEqual(float(response.data["total_cost"]), VALID_CAR["price_per_minute"])  # минимум 1 минута
        self.assertEqual(self._car_status(), "free")

        # Списание — в фоне (ПР6): платёж ждёт обработчика очереди, задача поставлена.
        payments = self.client.get(f"/api/v1/bookings/{booking['id']}/payments").data["items"]
        self.assertEqual(
            [(p["status"], float(p["amount"])) for p in payments], [("pending", VALID_CAR["price_per_minute"])]
        )
        self.assertEqual(response.data["payment"]["task"]["status"], "READY")

    def test_cancelled_booking_has_no_payment(self):
        booking = self._book().data
        response = self.client.patch(f"/api/v1/bookings/{booking['id']}", {"status": "cancelled"}, format="json")
        self.assertIsNone(response.data["payment"])
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

    def test_list_bookings_filtered_by_status(self):
        booking = self._book().data
        self.assertEqual(self.client.get("/api/v1/bookings?status=active").data["total"], 1)
        self.client.patch(f"/api/v1/bookings/{booking['id']}", {"status": "cancelled"}, format="json")
        self.assertEqual(self.client.get("/api/v1/bookings?status=active").data["total"], 0)
        self.assertEqual(self.client.get("/api/v1/bookings?status=cancelled").data["total"], 1)

    # --- транзакции ---------------------------------------------------------
    def _assert_internal_error(self, response):
        """Сбой отдан клиенту как 500 в едином формате, без текста исключения и стектрейса."""
        self.assertEqual(response.status_code, status.HTTP_500_INTERNAL_SERVER_ERROR)
        self.assertEqual(response.json(), {
            "error": {"code": "internal_error", "message": "Internal server error", "details": []},
        })

    def test_failure_on_last_step_rolls_back_whole_booking(self):
        """Бронь уже записана, но смена статуса машины падает — откатывается всё."""
        with (
            mock.patch.object(car_repository, "update", side_effect=RuntimeError("boom")),
            self.assertLogs("config.exceptions", "ERROR"),
        ):
            self._assert_internal_error(self._book())
        self.assertEqual(Booking.objects.count(), 0)
        self.assertEqual(Car.objects.get(pk=self.car["id"]).status, "free")

    def test_failure_on_last_step_rolls_back_whole_finish(self):
        """Бронь закрыта, платёж и задача списания записаны, но журнал статусов падает — откатывается всё."""
        booking = self._book().data
        history_before = CarStatusChange.objects.count()
        with (
            mock.patch.object(car_status_history_repository, "add", side_effect=RuntimeError("boom")),
            self.assertLogs("config.exceptions", "ERROR"),
        ):
            self._assert_internal_error(
                self.client.patch(f"/api/v1/bookings/{booking['id']}", {"status": "finished"}, format="json")
            )
        self.assertEqual(Booking.objects.get(pk=booking["id"]).status, "active")
        self.assertEqual(Payment.objects.count(), 0)
        self.assertEqual(BackgroundTask.objects.count(), 0)  # задача ставится в той же транзакции — откатилась тоже
        self.assertEqual(Car.objects.get(pk=self.car["id"]).status, "booked")
        self.assertEqual(CarStatusChange.objects.count(), history_before)
