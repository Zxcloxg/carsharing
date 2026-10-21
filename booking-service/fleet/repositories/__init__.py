"""Слой доступа к данным: один модуль на сущность.

Только здесь есть обращения к ORM. Репозитории отдают наверх обычные словари,
поэтому бизнес-логика и маршруты не знают ни о моделях Django, ни о SQL.
Справочник опций (features, car_features) и инциденты ведут операторы в
админке Django, поэтому отдельных репозиториев для них нет.
"""
from .bookings import BookingRepository
from .car_models import CarModelRepository
from .car_status_history import CarStatusHistoryRepository
from .cars import CarRepository
from .customers import CustomerRepository
from .driver_licenses import DriverLicenseRepository
from .payments import PaymentRepository

car_model_repository = CarModelRepository()
car_repository = CarRepository()
car_status_history_repository = CarStatusHistoryRepository()
customer_repository = CustomerRepository()
driver_license_repository = DriverLicenseRepository()
booking_repository = BookingRepository()
payment_repository = PaymentRepository()
