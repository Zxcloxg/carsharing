"""Демонстрация транзакций: сбой на последнем шаге откатывает все шаги операции.

Сценарий 1 — бронирование (bookings, cars, car_status_history): сбой при смене
статуса машины уже после того, как бронь записана.
Сценарий 2 — завершение аренды (bookings, payments, cars, car_status_history):
сбой при записи в журнал статусов уже после того, как бронь закрыта, платёж
записан и машина освобождена.
"""
from unittest import mock

from django.core.management.base import BaseCommand

from fleet import services
from fleet.repositories import (
    booking_repository,
    car_repository,
    car_status_history_repository,
    customer_repository,
    payment_repository,
)

PHONE = "+77027654321"  # клиент из seed_demo с подтверждённым ВУ и без активной брони


class Command(BaseCommand):
    help = "Показывает откат транзакций бронирования и завершения аренды при ошибке на последнем шаге"

    def handle(self, *args, **options):
        car = next(iter(car_repository.search(status="free", limit=1)[0]), None)
        customer = customer_repository.find_by_phone(PHONE)
        if car is None or customer is None:
            self.stderr.write("Нет демо-данных: выполните python manage.py seed_demo")
            return
        self.car_id = car["id"]
        self.booking_scenario()
        self.finish_scenario()

    # --- сценарий 1 ---------------------------------------------------------
    def booking_scenario(self):
        self.stdout.write(self.style.MIGRATE_HEADING("Сценарий 1. Бронирование с ошибкой на шаге 4"))
        self._state("До операции")

        def add_and_report(**kwargs):
            booking = real_add(**kwargs)
            self.stdout.write(f"  шаг 3: INSERT INTO bookings -> бронь {booking['id'][:8]}… записана "
                              f"(внутри транзакции активных броней у машины: {self._active()})")
            return booking

        def failing_update(car_id, changes, **kwargs):
            self.stdout.write("  шаг 4: UPDATE cars SET status = 'booked' … -> сбой")
            raise RuntimeError("Имитация сбоя при смене статуса машины")

        real_add = booking_repository.add
        self.stdout.write("  шаг 1: SELECT … FROM cars FOR UPDATE — машина заблокирована, статус free")
        self.stdout.write("  шаг 2: проверка клиента — ВУ подтверждено, активных аренд нет")
        with mock.patch.object(booking_repository, "add", side_effect=add_and_report), \
                mock.patch.object(car_repository, "update", side_effect=failing_update):
            try:
                services.create_booking({"car_id": self.car_id, "customer_phone": PHONE})
            except RuntimeError as exc:
                self.stdout.write(self.style.ERROR(f"  транзакция прервана: {exc} -> ROLLBACK"))
        self._state("После операции")
        self.stdout.write(self.style.SUCCESS("Бронь из шага 3 не сохранилась, статус машины не изменился.\n"))

    # --- сценарий 2 ---------------------------------------------------------
    def finish_scenario(self):
        self.stdout.write(self.style.MIGRATE_HEADING("Сценарий 2. Завершение аренды с ошибкой на шаге 5"))
        booking = services.create_booking({"car_id": self.car_id, "customer_phone": PHONE})
        self.stdout.write(f"Подготовка: бронь {booking['id'][:8]}… создана (отдельная успешная транзакция)")
        self._state("До операции", booking["id"])

        def pay_and_report(**kwargs):
            payment = real_pay(**kwargs)
            self.stdout.write(f"  шаг 3: INSERT INTO payments -> платёж {payment['amount']} ₸ записан "
                              f"(внутри транзакции платежей: {payment_repository.count(booking_id=booking['id'])})")
            return payment

        def update_and_report(car_id, changes, **kwargs):
            car = real_update(car_id, changes, **kwargs)
            self.stdout.write(f"  шаг 4: UPDATE cars SET status = '{car['status']}' -> выполнено")
            return car

        def failing_log(**kwargs):
            self.stdout.write("  шаг 5: INSERT INTO car_status_history … -> сбой")
            raise RuntimeError("Имитация сбоя при записи в журнал статусов")

        real_pay, real_update = payment_repository.add, car_repository.update
        self.stdout.write("  шаг 1: SELECT … FROM bookings FOR UPDATE — бронь заблокирована")
        self.stdout.write("  шаг 2: UPDATE bookings SET status = 'finished', total_cost = … -> выполнено")
        with mock.patch.object(payment_repository, "add", side_effect=pay_and_report), \
                mock.patch.object(car_repository, "update", side_effect=update_and_report), \
                mock.patch.object(car_status_history_repository, "add", side_effect=failing_log):
            try:
                services.close_booking(booking["id"], "finished")
            except RuntimeError as exc:
                self.stdout.write(self.style.ERROR(f"  транзакция прервана: {exc} -> ROLLBACK"))
        self._state("После операции", booking["id"])
        self.stdout.write(self.style.SUCCESS(
            "Бронь осталась активной, платежа нет, машина по-прежнему booked: откатились все 4 таблицы."
        ))
        services.close_booking(booking["id"], "cancelled")  # убираем за собой демо-бронь

    # --- вывод состояния ----------------------------------------------------
    def _active(self):
        return booking_repository.count(car_id=self.car_id, status="active")

    def _state(self, title, booking_id=None):
        car = car_repository.get(self.car_id)
        line = (f"{title}: машина {car['plate']} status={car['status']}, активных броней: {self._active()}, "
                f"записей в журнале статусов: {car_status_history_repository.count(car_id=self.car_id)}")
        if booking_id:
            booking = booking_repository.get(booking_id)
            line += (f", бронь status={booking['status']}, "
                     f"платежей: {payment_repository.count(booking_id=booking_id)}")
        self.stdout.write(line)
