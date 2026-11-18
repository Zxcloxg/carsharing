"""python manage.py run_worker — обработчик очереди фоновых задач.

Запускается отдельным процессом рядом с API (Windows: worker.cmd). Можно
запустить несколько обработчиков: задачи между ними не дублируются
(SELECT … FOR UPDATE SKIP LOCKED, jobs/worker.py).
"""
from django.core.management.base import BaseCommand

from jobs.worker import Worker


class Command(BaseCommand):
    help = "Выполняет фоновые задачи из очереди background_tasks (Ctrl+C — остановить)."

    def add_arguments(self, parser):
        parser.add_argument("--interval", type=float, default=1.0, help="Пауза опроса пустой очереди, с (по умолчанию 1)")
        parser.add_argument("--queue", action="append", dest="queues", help="Очередь (можно несколько раз); по умолчанию все")
        parser.add_argument("--once", action="store_true", help="Выполнить готовые задачи и выйти")

    def handle(self, *args, interval, queues, once, **options):
        worker = Worker(queues=queues)
        if once:
            done = worker.run_pending()
            self.stdout.write(f"Выполнено задач: {done}")
        else:
            worker.run(interval=interval)
