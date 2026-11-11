"""API-тесты ресурса /api/v1/cars: CRUD, валидация, пагинация и фильтры, журнал статусов (ПР2-ПР3)."""
from rest_framework import status

from fleet.models import CarModel
from tests.helpers import VALID_CAR, StaffApiTestCase


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

    def test_patch_brand_moves_car_to_other_model(self):
        car = self._create()
        response = self.client.patch(f"/api/v1/cars/{car['id']}", {"model": "Camry"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual((response.data["brand"], response.data["model"]), ("Toyota", "Camry"))
        self.assertEqual(CarModel.objects.count(), 2)

    def test_patch_plate_to_taken_one_returns_409(self):
        car = self._create()
        self._create(plate="002AAA02")
        response = self.client.patch(f"/api/v1/cars/{car['id']}", {"plate": "002AAA02"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)

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

    def test_list_filters_by_max_price(self):
        self._create(plate="111AAA02", price_per_minute=60)
        self._create(plate="222AAA02", price_per_minute=120)
        response = self.client.get("/api/v1/cars?max_price_per_minute=80")
        self.assertEqual([c["plate"] for c in response.data["items"]], ["111AAA02"])

    def test_invalid_query_param_returns_400(self):
        response = self.client.get("/api/v1/cars?status=flying")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
