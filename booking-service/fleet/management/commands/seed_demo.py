"""Наполняет пустую базу демо-данными для ручной проверки API и админки.

Машины, клиенты, заявки на ВУ и аренды создаются через сервисы — так же, как
через API, со всеми проверками и записью в журнал статусов. Справочник опций
и инциденты ведут операторы в админке, поэтому они пишутся моделями напрямую.

Учётные записи API (по одной на роль, клиенты привязаны к профилям)
добавляются отдельно — если таблица users пуста. Пароли демо-аккаунтов
приведены в README и предназначены только для локальной проверки.
"""
from datetime import date

from django.core.management.base import BaseCommand
from django.db import transaction

from accounts import services as account_services
from accounts.models import Role
from accounts.repository import UserRepository
from fleet import services
from fleet.models import Car, CarFeature, CarModel, Feature, Incident
from fleet.repositories import car_repository, customer_repository

CAR_MODELS = [
    ("Kia", "Rio", "sedan", 5, "petrol"),
    ("Hyundai", "Creta", "crossover", 5, "petrol"),
    ("Kia", "Seltos", "crossover", 5, "petrol"),
    ("Toyota", "Camry", "sedan", 5, "hybrid"),
    ("BYD", "Han", "sedan", 5, "electric"),
]

FEATURES = [
    ("child_seat", "Детское кресло"),
    ("awd", "Полный привод"),
    ("winter_tires", "Зимняя резина"),
    ("transponder", "Транспондер для платных дорог"),
    ("pet_friendly", "Можно с животными"),
]

CARS = [
    ({"plate": "123ABC02", "brand": "Kia", "model": "Rio", "year": 2022, "color": "white",
      "price_per_minute": 65, "fuel_level": 0.8}, ["winter_tires"]),
    ({"plate": "456DEF02", "brand": "Hyundai", "model": "Creta", "year": 2023, "color": "grey",
      "price_per_minute": 95, "fuel_level": 0.55}, ["child_seat", "awd", "winter_tires"]),
    ({"plate": "789GHI02", "brand": "Kia", "model": "Seltos", "year": 2021, "color": "blue",
      "price_per_minute": 90, "fuel_level": 0.3, "status": "service"}, ["awd"]),
    ({"plate": "321JKL02", "brand": "Toyota", "model": "Camry", "year": 2024, "color": "black",
      "price_per_minute": 130, "fuel_level": 0.9}, ["transponder", "child_seat"]),
    ({"plate": "654MNO02", "brand": "BYD", "model": "Han", "year": 2024, "color": "silver",
      "price_per_minute": 120, "fuel_level": 0.75}, ["pet_friendly"]),
]

CUSTOMERS = [
    # (данные клиента, данные ВУ, решение оператора)
    ({"phone": "+77011234567", "full_name": "Айгерим Сапарова", "birth_date": date(1995, 4, 12),
      "email": "aigerim@example.kz"},
     {"number": "KZ0123456", "categories": "B", "issued_on": date(2018, 6, 1), "expires_on": date(2028, 6, 1)},
     {"status": "approved"}),
    ({"phone": "+77027654321", "full_name": "Данияр Ахметов", "birth_date": date(1988, 11, 3),
      "email": "daniyar@example.kz"},
     {"number": "KZ7654321", "categories": "B,C", "issued_on": date(2010, 3, 15), "expires_on": date(2030, 3, 15)},
     {"status": "approved"}),
    ({"phone": "+77071112233", "full_name": "Мадина Жунусова", "birth_date": date(2001, 7, 25), "email": None},
     {"number": "KZ1112233", "categories": "B", "issued_on": date(2024, 9, 1), "expires_on": date(2034, 9, 1)},
     None),
    ({"phone": "+77779998877", "full_name": "Ерлан Касымов", "birth_date": date(1999, 1, 30), "email": None},
     {"number": "KZ9998877", "categories": "B", "issued_on": date(2019, 5, 20), "expires_on": date(2029, 5, 20)},
     {"status": "rejected", "reject_reason": "Фото удостоверения нечитаемо"}),
]

USERS = [
    # (email, пароль, роль, телефон профиля клиента)
    ("admin@carsharing.kz", "Steppe#Falcon2026", Role.ADMIN, None),
    ("operator@carsharing.kz", "Orbit#Signal2026", Role.OPERATOR, None),
    ("aigerim@example.kz", "Tulip#Morning2026", Role.CUSTOMER, "+77011234567"),
    ("daniyar@example.kz", "River#Stone2026", Role.CUSTOMER, "+77027654321"),
]


class Command(BaseCommand):
    help = "Добавляет демо-данные во все таблицы: автопарк — если он пуст, учётные записи — если их нет"

    @transaction.atomic
    def handle(self, *args, **options):
        if car_repository.search(limit=1)[1]:
            self.stdout.write("Автопарк не пуст — демо-данные автопарка не добавлены")
        else:
            self._seed_fleet()
        if UserRepository().search(limit=1)[1]:
            self.stdout.write("Учётные записи уже есть — демо-пользователи не добавлены")
        else:
            self._seed_users()

    def _seed_users(self):
        users = UserRepository()
        for email, password, role, phone in USERS:
            user = account_services.create_user(email, password, role)
            customer = customer_repository.find_by_phone(phone) if phone else None
            if customer:
                users.update(user["id"], {"customer_id": customer["id"]})
        self.stdout.write(self.style.SUCCESS(
            "Добавлено учётных записей: " + ", ".join(f"{email} ({role})" for email, _, role, _ in USERS)
        ))

    def _seed_fleet(self):
        for brand, model, body_type, seats, fuel_type in CAR_MODELS:
            CarModel.objects.create(brand=brand, model=model, body_type=body_type, seats=seats, fuel_type=fuel_type)
        features = {code: Feature.objects.create(code=code, name=name) for code, name in FEATURES}

        cars = {}
        for data, options in CARS:
            car = services.create_car({"status": "free", **data})
            cars[car["plate"]] = car
            for code in options:
                CarFeature.objects.create(car_id=car["id"], feature=features[code])

        customers = {}
        for data, license_data, decision in CUSTOMERS:
            customer = services.create_customer(data)
            customers[customer["phone"]] = customer
            license_ = services.submit_license(customer["id"], license_data)
            if decision:
                services.review_license(license_["id"], decision)
        services.patch_customer(customers["+77779998877"]["id"], {"status": "blocked"})

        # Завершённая аренда с платежом и инцидентом по ней, а также текущая активная бронь.
        trip = services.create_booking({"car_id": cars["456DEF02"]["id"], "customer_phone": "+77027654321"})
        services.close_booking(trip["id"], "finished")
        Incident.objects.create(
            car_id=cars["456DEF02"]["id"], booking_id=trip["id"], customer_id=trip["customer_id"],
            kind="dirty", description="Клиент сообщил о мусоре в салоне после поездки",
        )
        services.create_booking({"car_id": cars["321JKL02"]["id"], "customer_phone": "+77011234567"})

        self.stdout.write(self.style.SUCCESS(
            f"Добавлено: моделей {len(CAR_MODELS)}, опций {len(FEATURES)}, машин {Car.objects.count()}, "
            f"клиентов {len(CUSTOMERS)}, аренд 2, инцидентов 1"
        ))
