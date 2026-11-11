"""API-тесты авторизации: одна операция от имени разных ролей и права владельца записи (ПР4)."""
from rest_framework import status

from accounts.models import Role
from fleet import services as fleet_services
from tests.helpers import ME, VALID_CAR, VALID_CUSTOMER, ApiTestCase, authenticate, make_account, make_customer


class RoleTests(ApiTestCase):
    """Одна и та же операция от имени анонима, клиента, оператора и администратора."""

    def setUp(self):
        super().setUp()
        self.accounts = {role: make_account(role) for role in Role.values}
        self.customer = make_customer()
        self.license = self.customer.driver_licenses.get()

    def _as(self, role):
        self.client.credentials()
        if role:
            authenticate(self.client, self.accounts[role])

    def _check(self, request, expected):
        for role, code in expected.items():
            with self.subTest(role=role or "anonymous"):
                self._as(role)
                self.assertEqual(request().status_code, code)

    def test_delete_car_only_admin(self):
        cars = iter(
            fleet_services.create_car({**VALID_CAR, "status": "free", "plate": f"00{i}DEL02"}) for i in range(4)
        )
        self._check(lambda: self.client.delete(f"/api/v1/cars/{next(cars)['id']}"), {
            None: 401, "customer": 403, "operator": 403, "admin": 204,
        })

    def test_create_car_only_admin(self):
        plates = iter(f"10{i}NEW02" for i in range(4))
        self._check(lambda: self.client.post("/api/v1/cars", {**VALID_CAR, "plate": next(plates)}, format="json"), {
            None: 401, "customer": 403, "operator": 403, "admin": 201,
        })

    def test_change_car_status_staff_only(self):
        car = fleet_services.create_car({**VALID_CAR, "status": "free"})
        statuses = iter(["service", "service", "service", "free"])
        self._check(
            lambda: self.client.patch(f"/api/v1/cars/{car['id']}", {"status": next(statuses)}, format="json"),
            {None: 401, "customer": 403, "operator": 200, "admin": 200},
        )

    def test_review_license_staff_only(self):
        self.license.status, self.license.reviewed_at = "pending", None
        self.license.save()
        self._check(
            lambda: self.client.patch(
                f"/api/v1/driver-licenses/{self.license.id}", {"status": "approved"}, format="json"
            ),
            {None: 401, "customer": 403, "operator": 200},
        )

    def test_list_customers_staff_only(self):
        self._check(lambda: self.client.get("/api/v1/customers"), {
            None: 401, "customer": 403, "operator": 200, "admin": 200,
        })

    def test_manage_users_only_admin(self):
        self._check(lambda: self.client.get("/api/v1/users"), {
            None: 401, "customer": 403, "operator": 403, "admin": 200,
        })

    def test_admin_filters_users_by_role(self):
        self._as("admin")
        response = self.client.get("/api/v1/users", {"role": "operator"})
        self.assertEqual([u["email"] for u in response.data["items"]], [self.accounts["operator"]["email"]])
        self.assertError(self.client.get("/api/v1/users", {"role": "root"}), 400, "validation_error")

    def test_403_names_required_role(self):
        self._as("operator")
        error = self.assertError(self.client.delete("/api/v1/cars/whatever"), 403, "permission_denied")
        self.assertIn("admin", error["details"][0]["message"])
        self.assertIn("operator", error["details"][0]["message"])

    def test_admin_promotes_and_blocks_user(self):
        self._as("admin")
        target = self.accounts["customer"]["id"]
        response = self.client.patch(f"/api/v1/users/{target}", {"role": "operator"}, format="json")
        self.assertEqual(response.data["role"], "operator")
        response = self.client.patch(f"/api/v1/users/{target}", {"is_active": False}, format="json")
        self.assertFalse(response.data["is_active"])

    def test_admin_cannot_demote_self(self):
        self._as("admin")
        response = self.client.patch(
            f"/api/v1/users/{self.accounts['admin']['id']}", {"role": "customer"}, format="json"
        )
        self.assertError(response, 409, "cannot_change_own_account")


class OwnershipTests(ApiTestCase):
    """Клиент работает только со своими данными, персонал — с любыми."""

    def setUp(self):
        super().setUp()
        self.car = fleet_services.create_car({**VALID_CAR, "status": "free"})
        self.alice = make_customer(phone="+77010000001")
        self.bob = make_customer(phone="+77010000002")
        self.alice_account = make_account(email="alice@test.kz", customer_id=self.alice.id)
        self.bob_account = make_account(email="bob@test.kz", customer_id=self.bob.id)
        self.operator = make_account(Role.OPERATOR)
        authenticate(self.client, self.alice_account)

    def _bob_booking(self):
        return fleet_services.create_booking({"car_id": self.car["id"], "customer_phone": "+77010000002"})

    def test_new_customer_fills_own_profile(self):
        authenticate(self.client, make_account(email="carol@test.kz"))
        response = self.client.post("/api/v1/customers", {**VALID_CUSTOMER, "email": None}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["email"], "carol@test.kz")  # по умолчанию — email учётной записи
        self.assertEqual(self.client.get(ME).data["customer_id"], response.data["id"])

        again = self.client.post("/api/v1/customers", {**VALID_CUSTOMER, "phone": "+77015550000"}, format="json")
        self.assertError(again, 409, "customer_profile_exists")

    def test_customer_reads_only_own_profile(self):
        self.assertEqual(self.client.get(f"/api/v1/customers/{self.alice.id}").status_code, 200)
        self.assertError(self.client.get(f"/api/v1/customers/{self.bob.id}"), 403, "permission_denied")

    def test_customer_cannot_submit_license_for_other(self):
        response = self.client.post(f"/api/v1/customers/{self.bob.id}/driver-licenses", {}, format="json")
        self.assertError(response, 403, "permission_denied")

    def test_customer_lists_only_own_licenses(self):
        own = self.client.get(f"/api/v1/customers/{self.alice.id}/driver-licenses")
        self.assertEqual(own.status_code, status.HTTP_200_OK)
        self.assertEqual([lic["customer_id"] for lic in own.data["items"]], [str(self.alice.id)])
        response = self.client.get(f"/api/v1/customers/{self.bob.id}/driver-licenses")
        self.assertError(response, 403, "permission_denied")

    def test_customer_books_for_self_without_phone(self):
        response = self.client.post("/api/v1/bookings", {"car_id": self.car["id"]}, format="json")
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data["customer_id"], str(self.alice.id))

    def test_customer_cannot_book_for_other(self):
        response = self.client.post(
            "/api/v1/bookings", {"car_id": self.car["id"], "customer_phone": "+77010000002"}, format="json"
        )
        self.assertError(response, 403, "permission_denied")

    def test_customer_without_profile_cannot_book(self):
        authenticate(self.client, make_account(email="noprofile@test.kz"))
        response = self.client.post("/api/v1/bookings", {"car_id": self.car["id"]}, format="json")
        self.assertError(response, 403, "customer_profile_required")

    def test_staff_must_name_customer(self):
        authenticate(self.client, self.operator)
        response = self.client.post("/api/v1/bookings", {"car_id": self.car["id"]}, format="json")
        self.assertError(response, 400, "validation_error")

    def test_customer_sees_only_own_bookings(self):
        self._bob_booking()
        self.assertEqual(self.client.get("/api/v1/bookings").data["total"], 0)
        response = self.client.get(f"/api/v1/bookings?customer_id={self.bob.id}")
        self.assertError(response, 403, "permission_denied")
        authenticate(self.client, self.operator)
        self.assertEqual(self.client.get("/api/v1/bookings").data["total"], 1)

    def test_customer_cannot_touch_other_booking(self):
        booking = self._bob_booking()
        url = f"/api/v1/bookings/{booking['id']}"
        self.assertError(self.client.get(url), 403, "permission_denied")
        self.assertError(self.client.get(f"{url}/payments"), 403, "permission_denied")
        self.assertError(self.client.patch(url, {"status": "cancelled"}, format="json"), 403, "permission_denied")

        authenticate(self.client, self.bob_account)
        self.assertEqual(self.client.patch(url, {"status": "cancelled"}, format="json").status_code, 200)

    def test_operator_closes_any_booking(self):
        booking = self._bob_booking()
        authenticate(self.client, self.operator)
        response = self.client.patch(f"/api/v1/bookings/{booking['id']}", {"status": "finished"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
