"""Доступ к справочнику car_models: поиск модели по марке и названию, добавление новой."""
from django.db import IntegrityError, transaction

from ..models import CarModel


def to_dict(car_model):
    return {
        "id": car_model.id,
        "brand": car_model.brand,
        "model": car_model.model,
        "body_type": car_model.body_type,
        "seats": car_model.seats,
        "fuel_type": car_model.fuel_type,
    }


class CarModelRepository:
    def find(self, brand, model):
        car_model = CarModel.objects.filter(brand__iexact=brand, model__iexact=model).first()
        return to_dict(car_model) if car_model else None

    def get_or_add(self, brand, model, **specs):
        """Модель из справочника без учёта регистра; новая марка/модель добавляется в справочник."""
        found = self.find(brand, model)
        if found:
            return found
        try:
            with transaction.atomic():
                return to_dict(CarModel.objects.create(brand=brand, model=model, **specs))
        except IntegrityError:
            # Ту же модель параллельно добавил другой запрос (car_models_brand_model_uniq).
            return self.find(brand, model)
