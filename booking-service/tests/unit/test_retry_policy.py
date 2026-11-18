"""Политика повторов фоновых задач (jobs/retry.py): пауза, окончательный сбой, текст причины."""
from django.test import SimpleTestCase

from fleet.payment_gateway import PaymentDeclined, PaymentProviderUnavailable
from jobs.retry import PermanentTaskError, backoff_delay, describe_error, is_final_failure


class BackoffDelayTests(SimpleTestCase):
    """Пауза перед повтором растёт вдвое и упирается в максимум."""

    def test_doubles_each_attempt(self):
        self.assertEqual([backoff_delay(n, 2, 300) for n in (1, 2, 3, 4)], [2, 4, 8, 16])

    def test_capped_by_maximum(self):
        self.assertEqual(backoff_delay(20, 2, 300), 300)

    def test_zero_base_means_immediate_retry(self):
        self.assertEqual(backoff_delay(3, 0, 300), 0)

    def test_attempt_numbering_starts_from_one(self):
        with self.assertRaises(ValueError):
            backoff_delay(0, 2, 300)


class FinalFailureTests(SimpleTestCase):
    """Когда задачу больше не повторять."""

    def test_temporary_error_is_retried_while_attempts_left(self):
        self.assertFalse(is_final_failure(PaymentProviderUnavailable("down"), attempt=1, max_attempts=3))
        self.assertFalse(is_final_failure(TimeoutError(), attempt=2, max_attempts=3))

    def test_last_attempt_is_final(self):
        self.assertTrue(is_final_failure(PaymentProviderUnavailable("down"), attempt=3, max_attempts=3))

    def test_permanent_error_is_final_at_once(self):
        self.assertTrue(is_final_failure(PaymentDeclined("402"), attempt=1, max_attempts=3))
        self.assertTrue(is_final_failure(PermanentTaskError(), attempt=1, max_attempts=5))

    def test_declined_is_permanent_and_unavailable_is_not(self):
        self.assertTrue(issubclass(PaymentDeclined, PermanentTaskError))
        self.assertFalse(issubclass(PaymentProviderUnavailable, PermanentTaskError))


class DescribeErrorTests(SimpleTestCase):
    def test_class_and_message(self):
        self.assertEqual(describe_error(PaymentDeclined("Insufficient funds")), "PaymentDeclined: Insufficient funds")

    def test_class_only_when_no_message(self):
        self.assertEqual(describe_error(TimeoutError()), "TimeoutError")
