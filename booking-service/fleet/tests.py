"""Автотесты REST API автопарка: CRUD, валидация, пагинация и фильтрация."""
from rest_framework import status
from rest_framework.test import APITestCase

from .repository import repository

VALID_CAR = {
    "plate": "001AAA02",
    "brand": "Toyota",
    "model": "Corolla",
    "year": 2024,
    "color": "black",
    "price_per_minute": 80,
    "fuel_level": 0.9,
}


class CarApiTests(APITestCase):
    def setUp(self):
        repository.clear()

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
        self.assertEqual(response.data["error"], "Car not found")
        self.assertTrue(response.data["details"])

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
        self.assertEqual(response.data["error"], "Validation failed")
        self.assertEqual([d["field"] for d in response.data["details"]], ["brand"])

    def test_wrong_type_returns_400(self):
        response = self.client.post("/api/v1/cars", {**VALID_CAR, "year": "недавно"}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual([d["field"] for d in response.data["details"]], ["year"])

    def test_non_positive_price_returns_400(self):
        response = self.client.post("/api/v1/cars", {**VALID_CAR, "price_per_minute": 0}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_duplicate_plate_returns_409(self):
        self._create()
        response = self.client.post("/api/v1/cars", VALID_CAR, format="json")
        self.assertEqual(response.status_code, status.HTTP_409_CONFLICT)
        self.assertEqual(response.data["error"], "Car with this plate already exists")

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
