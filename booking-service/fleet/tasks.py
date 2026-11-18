"""Фоновые задачи каршеринга (ПР6) — стандартный декоратор @task из django.tasks.

* charge_payment — списать деньги за поездку у платёжного провайдера. Ставится
  в очередь при завершении поездки (services.close_booking) и при повторной
  оплате (services.retry_payment);
* send_receipt — отправить клиенту электронный чек; ставится только после
  успешного списания.

Повторы и паузы между ними задаёт обработчик очереди (jobs/worker.py), а не
сама задача: задача просто бросает исключение. Временная ошибка провайдера
(PaymentProviderUnavailable) приводит к повтору, отказ банка (PaymentDeclined)
— к немедленному сбою.
"""
from django.dispatch import receiver
from django.tasks import TaskResultStatus, task
from django.tasks.signals import task_finished

from jobs import services as job_services

from . import services


@task
def charge_payment(payment_id):
    return services.process_payment(payment_id)


@task
def send_receipt(payment_id):
    return services.send_receipt(payment_id)


@receiver(task_finished, dispatch_uid="fleet.tasks.payment_failed")
def mark_payment_failed(sender, task_result, **kwargs):
    """Списание окончательно не удалось (попытки кончились или банк отказал) — платёж failed.

    Пока задача повторяется, платёж остаётся pending: клиент видит «оплата
    обрабатывается», а не ложный сбой.
    """
    if task_result.status != TaskResultStatus.FAILED or task_result.task.func is not charge_payment.func:
        return
    reason = job_services.get_task(task_result.id)["last_error"]
    services.fail_payment(task_result.kwargs["payment_id"], reason)
