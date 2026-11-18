"""API-тесты клиентов и верификации водительских удостоверений (ПР3)."""
from datetime import date, timedelta

from rest_framework import status

from tests.helpers import VALID_CUSTOMER, VALID_LICENSE, StaffApiTestCase


class CustomerApiTests(StaffApiTestCase):
    """Клиенты и проверка ВУ: регистрация профиля, одобрение и отклонение ВУ, конфликты."""

    def _register(self, **overrides):
        return self.client.post("/api/v1/customers", {**VALID_CUSTOMER, **overrides}, format="json")

    def _submit(self, customer_id, **overrides):
        return self.client.post(
            f"/api/v1/customers/{customer_id}/driver-licenses", {**VALID_LICENSE, **overrides}, format="json"
        )

    def test_operator_blocks_customer_and_filters_list(self):
        customer = self._register().data
        self._register(phone="+77017654321", email="other@example.kz")
        response = self.client.patch(f"/api/v1/customers/{customer['id']}", {"status": "blocked"}, format="json")
        self.assertEqual(response.data["status"], "blocked")
        blocked = self.client.get("/api/v1/customers?status=blocked").data
        self.assertEqual([c["id"] for c in blocked["items"]], [customer["id"]])
        self.assertEqual(self.client.get("/api/v1/customers").data["total"], 2)

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

    def test_reviewed_license_cannot_be_reviewed_again(self):
        """Негативный сценарий: решение оператора окончательное — одобренную заявку нельзя «перерешить»."""
        customer = self._register().data
        license_ = self._submit(customer["id"]).data
        url = f"/api/v1/driver-licenses/{license_['id']}"
        self.client.patch(url, {"status": "approved"}, format="json")
        response = self.client.patch(url, {"status": "rejected", "reject_reason": "Передумали"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["error"]["code"], "license_already_reviewed")
        self.assertTrue(self.client.get(f"/api/v1/customers/{customer['id']}").data["has_valid_license"])

    def test_same_license_cannot_be_approved_for_two_customers(self):
        """Одно физическое удостоверение — один допущенный к аренде аккаунт (защита от аренды по чужому ВУ)."""
        first = self._register().data
        second = self._register(phone="+77017654321", email="other@example.kz").data
        first_license = self._submit(first["id"]).data
        second_license = self._submit(second["id"]).data
        self.client.patch(f"/api/v1/driver-licenses/{first_license['id']}", {"status": "approved"}, format="json")
        response = self.client.patch(
            f"/api/v1/driver-licenses/{second_license['id']}", {"status": "approved"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["error"]["code"], "license_number_taken")
        self.assertEqual(self.client.get(f"/api/v1/driver-licenses/{second_license['id']}").data["status"], "pending")

    def test_review_unknown_license_returns_404(self):
        response = self.client.patch(
            "/api/v1/driver-licenses/00000000-0000-4000-8000-000000000000", {"status": "approved"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(response.data["error"]["code"], "license_not_found")
