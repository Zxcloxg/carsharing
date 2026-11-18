#!/bin/sh
# Точка входа контейнера booking-service: роль задаётся первым аргументом (command в docker-compose.yml).
set -e

case "$1" in
  api)
    # Production-сервер вместо runserver: без автоперезагрузки и отладки, с пулом потоков.
    exec gunicorn config.wsgi:application --config gunicorn.conf.py
    ;;
  worker)
    exec python manage.py run_worker --interval "${WORKER_POLL_INTERVAL:-1}"
    ;;
  migrate)
    # Схема применяется на каждом развёртывании; уже применённые миграции Django пропускает.
    python manage.py migrate --noinput
    if [ "${SEED_DEMO:-false}" = "true" ]; then
      # seed_demo ничего не дублирует: на непустой базе он только сообщает об этом.
      python manage.py seed_demo
    fi
    ;;
  provider)
    exec python payment_provider_stub.py --host 0.0.0.0 --port 8030 --latency "${PROVIDER_LATENCY:-1.5}"
    ;;
  *)
    # Любая другая команда: docker compose run --rm api python manage.py createsuperuser и т. п.
    exec "$@"
    ;;
esac
