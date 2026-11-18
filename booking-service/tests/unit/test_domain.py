"""Модульные тесты правил предметной области (fleet/domain.py).

SimpleTestCase запрещает обращения к базе данных: любой SQL-запрос из
проверяемого кода уронил бы тест. Сеть тоже не нужна — на вход подаются
готовые значения, включая «текущую» дату.
"""
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

from django.test import SimpleTestCase

from fleet import domain
from fleet.errors import CustomerCannotRent, CustomerHasActiveBooking

START = datetime(2026, 11, 11, 9, 0, tzinfo=timezone.utc)


class BillableMinutesTests(SimpleTestCase):
    """Тарификация поминутная: каждая начатая минута оплачивается целиком."""

    def test_exact_minutes(self):
        self.assertEqual(domain.billable_minutes(START, START + timedelta(minutes=25)), 25)

    def test_started_minute_is_charged_in_full(self):
        self.assertEqual(domain.billable_minutes(START, START + timedelta(minutes=25, seconds=1)), 26)

    def test_minimum_is_one_minute(self):
        """Бронь отменили или завершили сразу — списывается одна минута, а не ноль."""
        self.assertEqual(domain.billable_minutes(START, START), 1)
        self.assertEqual(domain.billable_minutes(START, START + timedelta(seconds=5)), 1)

    def test_finish_before_start_is_rejected(self):
        """Сбой часов не должен превратиться в бесплатную или «отрицательную» поездку."""
        with self.assertRaises(ValueError):
            domain.billable_minutes(START, START - timedelta(seconds=1))

    def test_day_long_rent(self):
        self.assertEqual(domain.billable_minutes(START, START + timedelta(days=1)), 24 * 60)


class RentalCostTests(SimpleTestCase):
    """Стоимость аренды: тариф брони × оплачиваемые минуты, точные деньги в Decimal."""

    def test_cost_is_price_times_minutes(self):
        cost = domain.rental_cost(Decimal("80.00"), START, START + timedelta(minutes=12, seconds=30))
        self.assertEqual(cost, Decimal("1040.00"))  # 13 минут по 80 ₸

    def test_cost_is_exact_decimal(self):
        """Деньги считаются в Decimal: никаких 0.1 + 0.2 = 0.30000000000000004."""
        cost = domain.rental_cost(Decimal("0.10"), START, START + timedelta(minutes=3))
        self.assertEqual(cost, Decimal("0.30"))
        self.assertIsInstance(cost, Decimal)

    def test_cost_has_two_decimal_places(self):
        cost = domain.rental_cost(Decimal("65.5"), START, START + timedelta(minutes=1))
        self.assertEqual(str(cost), "65.50")

    def test_price_fixed_at_booking_is_used(self):
        """Тариф берётся из брони: изменение цены машины во время поездки на неё не влияет."""
        booking_price, current_car_price = Decimal("70"), Decimal("150")
        cost = domain.rental_cost(booking_price, START, START + timedelta(minutes=10))
        self.assertEqual(cost, Decimal("700.00"))
        self.assertNotEqual(cost, current_car_price * 10)


class AgeTests(SimpleTestCase):
    """Полный возраст и допуск 18+, включая родившихся 29 февраля."""

    def test_age_counts_full_years(self):
        self.assertEqual(domain.age_on(date(2000, 6, 15), date(2026, 6, 14)), 25)
        self.assertEqual(domain.age_on(date(2000, 6, 15), date(2026, 6, 15)), 26)

    def test_adult_exactly_on_18th_birthday(self):
        self.assertFalse(domain.is_adult(date(2008, 11, 12), date(2026, 11, 11)))
        self.assertTrue(domain.is_adult(date(2008, 11, 11), date(2026, 11, 11)))

    def test_born_on_leap_day(self):
        """Родившийся 29 февраля в невисокосный год становится старше 1 марта."""
        self.assertEqual(domain.age_on(date(2008, 2, 29), date(2026, 2, 28)), 17)
        self.assertEqual(domain.age_on(date(2008, 2, 29), date(2026, 3, 1)), 18)


class CanRentTests(SimpleTestCase):
    """Допуск клиента к аренде (US-4): блокировка, ВУ, другая активная аренда."""

    def _customer(self, **overrides):
        return {"id": "c1", "status": "active", "has_valid_license": True, **overrides}

    def test_active_customer_with_license_may_rent(self):
        self.assertIsNone(domain.ensure_can_rent(self._customer(), has_active_booking=False))

    def test_blocked_customer_cannot_rent(self):
        with self.assertRaises(CustomerCannotRent) as ctx:
            domain.ensure_can_rent(self._customer(status="blocked"), has_active_booking=False)
        self.assertEqual(ctx.exception.status_code, 403)
        self.assertEqual(ctx.exception.details[0]["message"], "Customer is blocked")

    def test_blocking_is_checked_before_license(self):
        """Заблокированному клиенту без ВУ причина отказа — блокировка: её не исправить подачей ВУ."""
        with self.assertRaises(CustomerCannotRent) as ctx:
            domain.ensure_can_rent(self._customer(status="blocked", has_valid_license=False), False)
        self.assertEqual(ctx.exception.details[0]["message"], "Customer is blocked")

    def test_customer_without_verified_license_cannot_rent(self):
        with self.assertRaises(CustomerCannotRent) as ctx:
            domain.ensure_can_rent(self._customer(has_valid_license=False), has_active_booking=False)
        self.assertIn("Driver license", ctx.exception.details[0]["message"])

    def test_second_simultaneous_rent_is_conflict(self):
        with self.assertRaises(CustomerHasActiveBooking) as ctx:
            domain.ensure_can_rent(self._customer(), has_active_booking=True)
        self.assertEqual(ctx.exception.status_code, 409)


class LicenseRulesTests(SimpleTestCase):
    """Водительское удостоверение: категория B и корректные даты."""

    TODAY = date(2026, 11, 11)

    def test_categories_normalized(self):
        self.assertEqual(domain.normalize_categories(" b, c ,B"), "B,C")

    def test_category_b_required(self):
        self.assertTrue(domain.allows_car_rental("A,B"))
        self.assertTrue(domain.allows_car_rental("b"))
        self.assertFalse(domain.allows_car_rental("A,C"))

    def test_similar_categories_do_not_replace_b(self):
        """BE и C1 — другие категории; поиск подстроки «B» здесь был бы ошибкой."""
        self.assertFalse(domain.allows_car_rental("BE"))
        self.assertFalse(domain.allows_car_rental("C1,BE"))

    def test_valid_dates(self):
        self.assertEqual(domain.license_date_problems(date(2018, 6, 1), date(2028, 6, 1), self.TODAY), {})

    def test_issue_date_in_future(self):
        problems = domain.license_date_problems(date(2026, 12, 1), date(2036, 12, 1), self.TODAY)
        self.assertEqual(list(problems), ["issued_on"])

    def test_expiry_before_issue(self):
        problems = domain.license_date_problems(date(2020, 1, 1), date(2020, 1, 1), self.TODAY)
        self.assertEqual(list(problems), ["expires_on"])

    def test_expired_license(self):
        problems = domain.license_date_problems(date(2010, 1, 1), date(2026, 11, 10), self.TODAY)
        self.assertEqual(problems, {"expires_on": "Driver license has expired"})

    def test_license_expiring_today_is_still_valid(self):
        self.assertEqual(domain.license_date_problems(date(2016, 11, 11), self.TODAY, self.TODAY), {})


class PaginationTests(SimpleTestCase):
    """Смещение страницы и число страниц."""

    def test_offset(self):
        self.assertEqual(domain.page_offset(1, 10), 0)
        self.assertEqual(domain.page_offset(3, 25), 50)

    def test_invalid_page_rejected(self):
        for page, limit in ((0, 10), (1, 0), (-1, 5)):
            with self.subTest(page=page, limit=limit), self.assertRaises(ValueError):
                domain.page_offset(page, limit)

    def test_pages_rounded_up(self):
        self.assertEqual(domain.page_envelope([], 11, 1, 5)["pages"], 3)
        self.assertEqual(domain.page_envelope([], 10, 1, 5)["pages"], 2)

    def test_empty_list_has_zero_pages(self):
        self.assertEqual(domain.page_envelope([], 0, 1, 10), {"items": [], "page": 1, "limit": 10, "total": 0, "pages": 0})


class CarRulesTests(SimpleTestCase):
    """Нормализация госномера и допустимый год выпуска."""

    def test_plate_normalized(self):
        self.assertEqual(domain.normalize_plate("  123abc02 "), "123ABC02")

    def test_next_model_year_allowed(self):
        self.assertEqual(domain.max_model_year(date(2026, 11, 11)), 2027)
