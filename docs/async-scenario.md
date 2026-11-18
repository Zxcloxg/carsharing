# Протокол проверки фонового списания (ПР6)

Дата прогона: 2026-11-18 12:58:26. Протокол сформирован скриптом `booking-service/async_check.py` на запущенных процессах: API (`runserver 8010`), обработчик очереди (`run_worker`) и эмулятор платёжного провайдера (`payment_provider_stub.py`, порт 8030). Все ответы, времена и строки логов ниже — из этого прогона.

Параметры: попыток на задачу — 3, пауза перед повтором — 2 с, затем 4 с (удваивается), тайм-аут вызова провайдера — 5 с, эмулятор отвечает на списание за 1.5 с (так ведёт себя настоящий банк-эквайер).

Клиент: `daniyar@example.kz` (роль `customer`), статус очереди смотрит оператор `operator@carsharing.kz`.

## 1. Штатный путь: провайдер работает

1.1. Клиент бронирует машину 123ABC02 (Kia Rio, 65.0 ₸/мин): `POST /api/v1/bookings` -> **201**, бронь `63afe963…`.

1.2. Клиент завершает поездку: `PATCH /api/v1/bookings/63afe963…` `{"status": "finished"}` -> **200** за **251 мс**. Поездка закрыта и машина свободна сразу, а списание 65.0 ₸ только поставлено в очередь — платёж `pending`, задача `READY`:

```json
{
  "id": "63afe963-ab8b-4d54-8839-c8e655abc2e3",
  "status": "finished",
  "total_cost": 65.0,
  "finished_at": "2026-11-18T06:58:27.122623+00:00",
  "payment": {
    "id": "a29dcc63-47e8-44d1-976d-f90e1feb083b",
    "booking_id": "63afe963-ab8b-4d54-8839-c8e655abc2e3",
    "amount": 65.0,
    "status": "pending",
    "provider_payment_id": null,
    "failure_reason": null,
    "task_id": "1af6e8d9-5395-47c8-9d8e-aaefd4649a5c",
    "created_at": "2026-11-18T06:58:27.131603+00:00",
    "paid_at": null,
    "task": {
      "id": "1af6e8d9-5395-47c8-9d8e-aaefd4649a5c",
      "status": "READY",
      "attempts": 0,
      "max_attempts": 3,
      "next_attempt_at": "2026-11-18T06:58:27.138595+00:00",
      "last_error": null,
      "finished_at": null
    }
  }
}
```

1.3. Клиент опрашивает `GET /api/v1/payments/{id}` (адрес из ответа) — как менялся статус (время от отправки запроса на завершение):

| t, с | Платёж | Задача | Попытка | Следующая попытка | Последняя ошибка | Ответ API, мс |
|---|---|---|---|---|---|---|
| 0.4 | `pending` | `READY` | 0/3 | 06:58:27 | — | 187 |
| 1.2 | `pending` | `RUNNING` | 1/3 | — | — | 192 |
| 2.8 | `paid` | `SUCCESSFUL` | 1/3 | — | — | 189 |

Итог: платёж **`paid`**, операция провайдера `ch_742180b392444c8da533`, задача выполнена с попытки 1.

1.4. Оператор смотрит очередь: `GET /api/v1/tasks?limit=2` (две последние задачи — списание и чек, который ставится только после успешного списания):

```json
[
  {
    "task": "send_receipt",
    "status": "SUCCESSFUL",
    "attempts": 1,
    "max_attempts": 3,
    "finished_at": "2026-11-18T06:58:30.657330+00:00",
    "return_value": {
      "to": "daniyar@example.kz",
      "sent": true
    }
  },
  {
    "task": "charge_payment",
    "status": "SUCCESSFUL",
    "attempts": 1,
    "max_attempts": 3,
    "finished_at": "2026-11-18T06:58:29.491930+00:00",
    "return_value": {
      "status": "paid",
      "payment_id": "a29dcc63-47e8-44d1-976d-f90e1feb083b",
      "provider_payment_id": "ch_742180b392444c8da533"
    }
  }
]
```

Для сравнения: если бы списание шло прямо в запросе (как было до ПР6), ответ на завершение поездки ждал бы банк — не меньше 1.5 с вместо 251 мс.

1.5. Лог обработчика (`run_worker`) за этот путь:

```text
[2026-11-18 12:58:27,754] INFO jobs: Обработчик portnova:12172 запущен: очереди default, опрос каждые 0.5 с, попыток на задачу 3, пауза повтора от 2 с
[2026-11-18 12:58:27,897] INFO jobs: Задача 1af6e8d9 (charge_payment) — попытка 1 из 3, обработчик portnova:12172
[2026-11-18 12:58:27,945] INFO fleet.payments: Платёж a29dcc63: запрос к провайдеру http://127.0.0.1:8030/v1/charges, сумма 65.00 ₸, тайм-аут 5 с
[2026-11-18 12:58:29,471] INFO fleet.payments: Платёж a29dcc63: провайдер подтвердил списание ch_742180b392444c8da533 за 1.53 с
[2026-11-18 12:58:29,482] INFO fleet.payments: Платёж a29dcc63 оплачен: 65.00 ₸, операция ch_742180b392444c8da533
[2026-11-18 12:58:29,489] INFO jobs: Задача send_receipt поставлена в очередь: id=67ced031, аргументы payment_id=a29dcc63-47e8-44d1-976d-f90e1feb083b, попыток не больше 3
[2026-11-18 12:58:29,495] INFO jobs: Задача 1af6e8d9 (charge_payment) выполнена за 1.59 с с попытки 1: {'payment_id': 'a29dcc63-47e8-44d1-976d-f90e1feb083b', 'status': 'paid', 'provider_payment_id': 'ch_742180b392444c8da533'}
[2026-11-18 12:58:29,507] INFO jobs: Задача 67ced031 (send_receipt) — попытка 1 из 3, обработчик portnova:12172
Content-Type: text/plain; charset="utf-8"
Content-Transfer-Encoding: 8bit
MIME-Version: 1.0
Subject: =?utf-8?b?0JrQsNGA0YjQtdGA0LjQvdCzOiDRh9C10Log0LfQsCDQv9C+0LXQt9C0?=
 =?utf-8?q?=D0=BA=D1=83?= 63afe963
From: receipts@carsharing.kz
To: daniyar@example.kz
Date: Wed, 18 Nov 2026 06:58:29 +0000
Message-ID: <179498510958.12172.856150621548597391@portnova.vols.kz>
Поездка завершена 2026-11-18T06:58:27.122623+00:00.
Списано: 65.00 ₸ (операция ch_742180b392444c8da533).
Спасибо, что выбрали нас!
-------------------------------------------------------------------------------
[2026-11-18 12:58:30,656] INFO fleet.payments: Чек по платежу a29dcc63 отправлен на daniyar@example.kz
[2026-11-18 12:58:30,661] INFO jobs: Задача 67ced031 (send_receipt) выполнена за 1.16 с с попытки 1: {'sent': True, 'to': 'daniyar@example.kz'}
```

Лог эмулятора провайдера:

```text
[2026-11-18 12:58:29] provider: #1 списание a29dcc63 на 65.00 ₸ -> 201 ch_742180b392444c8da533 (за 1.5 с)
```

## 2. Ошибочный путь: провайдер выключен

2.1. Процесс эмулятора провайдера принудительно остановлен (`terminate`) — на порту 8030 никто не слушает, любое подключение получает отказ. (На Windows отказ приходит не сразу, а примерно через 2 с — система сама повторяет попытку соединения; это видно по длительности попыток в логе.)

2.2. Клиент бронирует машину 123ABC02 (Kia Rio, 65.0 ₸/мин): `POST /api/v1/bookings` -> **201**, бронь `9ba79f5e…`.

2.3. Завершение поездки: `PATCH /api/v1/bookings/9ba79f5e…` -> **200** за **254 мс** — отказ провайдера на ответ клиенту не влияет, платёж `pending`.

2.4. Статус платежа по времени — задача повторяется с паузой 2 с, потом 4 с; пока повторы идут, платёж остаётся `pending` (клиент видит «оплата обрабатывается», а не ложный сбой):

| t, с | Платёж | Задача | Попытка | Следующая попытка | Последняя ошибка | Ответ API, мс |
|---|---|---|---|---|---|---|
| 0.5 | `pending` | `READY` | 0/3 | 06:58:32 | — | 293 |
| 0.9 | `pending` | `RUNNING` | 1/3 | — | — | 176 |
| 3.1 | `pending` | `READY` | 1/3 | 06:58:36 | PaymentProviderUnavailable: Provider is unreachable at http://127.0.0.1:8030/v1/charges… | 217 |
| 5.0 | `pending` | `RUNNING` | 2/3 | — | PaymentProviderUnavailable: Provider is unreachable at http://127.0.0.1:8030/v1/charges… | 226 |
| 7.3 | `pending` | `READY` | 2/3 | 06:58:42 | PaymentProviderUnavailable: Provider is unreachable at http://127.0.0.1:8030/v1/charges… | 224 |
| 11.1 | `pending` | `RUNNING` | 3/3 | — | PaymentProviderUnavailable: Provider is unreachable at http://127.0.0.1:8030/v1/charges… | 173 |
| 13.2 | `failed` | `FAILED` | 3/3 | — | PaymentProviderUnavailable: Provider is unreachable at http://127.0.0.1:8030/v1/charges… | 167 |

2.5. API во время сбоя отвечает как обычно (каталог машин запрашивался между попытками):

| t, с | Запрос | Код | Время ответа, мс | Попыток списания к этому моменту |
|---|---|---|---|---|
| 1.0 | `GET /api/v1/cars?status=free` | 200 | 118 | 1 |
| 5.1 | `GET /api/v1/cars?status=free` | 200 | 139 | 2 |
| 11.2 | `GET /api/v1/cars?status=free` | 200 | 107 | 3 |

2.6. Итоговое состояние платежа — `failed` с причиной; задача `FAILED` после 3 попыток:

```json
{
  "id": "dc03976e-ee94-491d-af37-7d568e298d24",
  "status": "failed",
  "amount": 65.0,
  "failure_reason": "PaymentProviderUnavailable: Provider is unreachable at http://127.0.0.1:8030/v1/charges: [WinError 10061] Подключение не установлено, т.к. конечный компьютер отверг запрос на подключение",
  "paid_at": null,
  "task": {
    "id": "d236f42f-937d-43a9-83ff-e9136e64637e",
    "status": "FAILED",
    "attempts": 3,
    "max_attempts": 3,
    "next_attempt_at": null,
    "last_error": "PaymentProviderUnavailable: Provider is unreachable at http://127.0.0.1:8030/v1/charges: [WinError 10061] Подключение не установлено, т.к. конечный компьютер отверг запрос на подключение",
    "finished_at": "2026-11-18T06:58:45.041882+00:00"
  }
}
```

Оператор видит причину каждой попытки (`GET /api/v1/tasks/{id}`, без стектрейсов — они только в базе и админке):

```json
{
  "task": "charge_payment",
  "status": "FAILED",
  "attempts": 3,
  "max_attempts": 3,
  "errors": [
    {
      "attempt": 1,
      "at": "2026-11-18T06:58:34.774138+00:00",
      "message": "PaymentProviderUnavailable: Provider is unreachable at http://127.0.0.1:8030/v1/charges: [WinError 10061] Подключение не установлено, т.к. конечный компьютер отверг запрос на подключение"
    },
    {
      "attempt": 2,
      "at": "2026-11-18T06:58:38.888126+00:00",
      "message": "PaymentProviderUnavailable: Provider is unreachable at http://127.0.0.1:8030/v1/charges: [WinError 10061] Подключение не установлено, т.к. конечный компьютер отверг запрос на подключение"
    },
    {
      "attempt": 3,
      "at": "2026-11-18T06:58:45.037879+00:00",
      "message": "PaymentProviderUnavailable: Provider is unreachable at http://127.0.0.1:8030/v1/charges: [WinError 10061] Подключение не установлено, т.к. конечный компьютер отверг запрос на подключение"
    }
  ]
}
```

2.7. Лог обработчика за ошибочный путь — постановка, три попытки с причинами, окончательный сбой и перевод платежа в `failed`:

```text
[2026-11-18 12:58:32,709] INFO jobs: Задача d236f42f (charge_payment) — попытка 1 из 3, обработчик portnova:12172
[2026-11-18 12:58:32,719] INFO fleet.payments: Платёж dc03976e: запрос к провайдеру http://127.0.0.1:8030/v1/charges, сумма 65.00 ₸, тайм-аут 5 с
[2026-11-18 12:58:34,805] WARNING jobs: Задача d236f42f (charge_payment) — сбой попытки 1 из 3 через 2.06 с: PaymentProviderUnavailable: Provider is unreachable at http://127.0.0.1:8030/v1/charges: [WinError 10061] Подключение не установлено, т.к. конечный компьютер отверг запрос на подключение. Повтор через 2 с
[2026-11-18 12:58:36,855] INFO jobs: Задача d236f42f (charge_payment) — попытка 2 из 3, обработчик portnova:12172
[2026-11-18 12:58:36,863] INFO fleet.payments: Платёж dc03976e: запрос к провайдеру http://127.0.0.1:8030/v1/charges, сумма 65.00 ₸, тайм-аут 5 с
[2026-11-18 12:58:38,897] WARNING jobs: Задача d236f42f (charge_payment) — сбой попытки 2 из 3 через 2.03 с: PaymentProviderUnavailable: Provider is unreachable at http://127.0.0.1:8030/v1/charges: [WinError 10061] Подключение не установлено, т.к. конечный компьютер отверг запрос на подключение. Повтор через 4 с
[2026-11-18 12:58:42,970] INFO jobs: Задача d236f42f (charge_payment) — попытка 3 из 3, обработчик portnova:12172
[2026-11-18 12:58:42,977] INFO fleet.payments: Платёж dc03976e: запрос к провайдеру http://127.0.0.1:8030/v1/charges, сумма 65.00 ₸, тайм-аут 5 с
[2026-11-18 12:58:45,040] ERROR jobs: Задача d236f42f (charge_payment) завершилась сбоем на попытке 3 из 3 (попытки исчерпаны): PaymentProviderUnavailable: Provider is unreachable at http://127.0.0.1:8030/v1/charges: [WinError 10061] Подключение не установлено, т.к. конечный компьютер отверг запрос на подключение
[2026-11-18 12:58:45,061] ERROR fleet.payments: Платёж dc03976e не прошёл: PaymentProviderUnavailable: Provider is unreachable at http://127.0.0.1:8030/v1/charges: [WinError 10061] Подключение не установлено, т.к. конечный компьютер отверг запрос на подключение. Повторить оплату: POST /api/v1/bookings/9ba79f5e-e96b-4c02-bd90-8556670579d8/payments
```

Процессы API и обработчика после сбоя: работают — сбой зависимости не роняет приложение.

## 3. Восстановление: провайдер снова доступен, клиент повторяет оплату

3.1. Эмулятор провайдера запущен заново с ключом `--fail-first 1`: первое списание он отклонит ответом 503 (перегрузка), следующие проведёт.

3.2. Клиент повторяет оплату: `POST /api/v1/bookings/9ba79f5e…/payments` -> **202 Accepted** за 219 мс, `Location: /api/v1/payments/62775af4-86d7-49ef-80e7-2cdaeaaa2bd5`:

```json
{
  "id": "62775af4-86d7-49ef-80e7-2cdaeaaa2bd5",
  "status": "pending",
  "amount": 65.0,
  "task_id": "52839088-9ac9-4d25-9807-1ec41ff032ec",
  "task": {
    "id": "52839088-9ac9-4d25-9807-1ec41ff032ec",
    "status": "READY",
    "attempts": 0,
    "max_attempts": 3,
    "next_attempt_at": "2026-11-18T06:58:46.022088+00:00",
    "last_error": null,
    "finished_at": null
  }
}
```

Повторный запрос, пока списание идёт, отклоняется: **409** `payment_in_progress` — двух параллельных списаний за одну поездку не бывает.

3.3. Статус нового платежа по времени: попытка 1 получила 503, попытка 2 через 2 с прошла:

| t, с | Платёж | Задача | Попытка | Следующая попытка | Последняя ошибка | Ответ API, мс |
|---|---|---|---|---|---|---|
| 0.8 | `pending` | `RUNNING` | 1/3 | — | — | 215 |
| 2.1 | `pending` | `READY` | 1/3 | 06:58:49 | PaymentProviderUnavailable: Provider answered 503: Service temporarily unavailable | 223 |
| 4.1 | `pending` | `RUNNING` | 2/3 | — | PaymentProviderUnavailable: Provider answered 503: Service temporarily unavailable | 209 |
| 5.7 | `paid` | `SUCCESSFUL` | 2/3 | — | PaymentProviderUnavailable: Provider answered 503: Service temporarily unavailable | 188 |

3.4. История платежей поездки — неуспешный платёж сохранён, успешный один:

| Платёж | Статус | Сумма | Причина сбоя / операция провайдера |
|---|---|---|---|
| `dc03976e…` | `failed` | 65.0 | PaymentProviderUnavailable: Provider is unreachable at http://127.0.0.1:8030/v1/charges: [WinError 10061] Подключение не установлено, т.к. конечный компьютер отверг запрос на подключение |
| `62775af4…` | `paid` | 65.0 | ch_017eb708c66e4dc8afbc |

3.5. Лог обработчика (текст письма с чеком, который консольный почтовый бэкенд печатает в тот же поток, здесь опущен — он показан в п. 1.5):

```text
[2026-11-18 12:58:46,092] INFO jobs: Задача 52839088 (charge_payment) — попытка 1 из 3, обработчик portnova:12172
[2026-11-18 12:58:46,099] INFO fleet.payments: Платёж 62775af4: запрос к провайдеру http://127.0.0.1:8030/v1/charges, сумма 65.00 ₸, тайм-аут 5 с
[2026-11-18 12:58:47,616] WARNING jobs: Задача 52839088 (charge_payment) — сбой попытки 1 из 3 через 1.51 с: PaymentProviderUnavailable: Provider answered 503: Service temporarily unavailable. Повтор через 2 с
[2026-11-18 12:58:49,663] INFO jobs: Задача 52839088 (charge_payment) — попытка 2 из 3, обработчик portnova:12172
[2026-11-18 12:58:49,671] INFO fleet.payments: Платёж 62775af4: запрос к провайдеру http://127.0.0.1:8030/v1/charges, сумма 65.00 ₸, тайм-аут 5 с
[2026-11-18 12:58:51,192] INFO fleet.payments: Платёж 62775af4: провайдер подтвердил списание ch_017eb708c66e4dc8afbc за 1.51 с
[2026-11-18 12:58:51,200] INFO fleet.payments: Платёж 62775af4 оплачен: 65.00 ₸, операция ch_017eb708c66e4dc8afbc
[2026-11-18 12:58:51,204] INFO jobs: Задача send_receipt поставлена в очередь: id=539699e0, аргументы payment_id=62775af4-86d7-49ef-80e7-2cdaeaaa2bd5, попыток не больше 3
[2026-11-18 12:58:51,210] INFO jobs: Задача 52839088 (charge_payment) выполнена за 1.55 с с попытки 2: {'payment_id': '62775af4-86d7-49ef-80e7-2cdaeaaa2bd5', 'status': 'paid', 'provider_payment_id': 'ch_017eb708c66e4dc8afbc'}
[2026-11-18 12:58:51,220] INFO jobs: Задача 539699e0 (send_receipt) — попытка 1 из 3, обработчик portnova:12172
[2026-11-18 12:58:51,240] INFO fleet.payments: Чек по платежу 62775af4 отправлен на daniyar@example.kz
[2026-11-18 12:58:51,243] INFO jobs: Задача 539699e0 (send_receipt) выполнена за 0.01 с с попытки 1: {'sent': True, 'to': 'daniyar@example.kz'}
```

Лог эмулятора провайдера:

```text
[2026-11-18 12:58:47] provider: #1 списание 62775af4 на 65.00 ₸ -> 503 (имитация перегрузки)
[2026-11-18 12:58:51] provider: #2 списание 62775af4 на 65.00 ₸ -> 201 ch_017eb708c66e4dc8afbc (за 1.5 с)
```

## Вывод

* Штатный путь: ответ на завершение поездки не ждёт банк; списание выполняется отдельно от запроса, его статус и результат видны через `GET /api/v1/payments/{id}` и `GET /api/v1/tasks/{id}`.
* Ошибочный путь: при выключенном провайдере задача повторяется ограниченное число раз с растущей паузой, причина каждой попытки записана в лог и в задачу; после исчерпания попыток платёж переходит в `failed` с понятной причиной, API и обработчик продолжают работать.
* Восстановление: клиент повторяет оплату (202 Accepted), временный 503 провайдера переживается повтором, двойного списания нет.
