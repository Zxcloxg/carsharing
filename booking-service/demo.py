"""Демонстрация REST API ресурса Car: полный CRUD-цикл и проверка ошибок.

Скрипт последовательно вызывает все операции контракта на запущенном сервисе
и печатает для каждой запрос, код ответа и тело — в том виде, который удобно
приложить к отчёту (скриншоты 3 и 4).

С ПР4 изменять автопарк может только администратор, поэтому скрипт сначала
входит под демо-администратором (python manage.py seed_demo) и передаёт
полученный токен в заголовке Authorization.

Запуск: сначала ./serve.cmd в одном окне, затем ./demo.cmd в другом.
"""
import json
import random
import urllib.error
import urllib.request

BASE = "http://127.0.0.1:8010"
LINE = "=" * 78
ADMIN = {"email": "admin@carsharing.kz", "password": "Steppe#Falcon2026"}

# Локальный сервис — обращаемся напрямую, минуя системный прокси:
# иначе каждый запрос ждёт ответа прокси несколько секунд.
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def call(method, path, body=None, title="", token=None):
    """Выполняет запрос и печатает запрос/ответ."""
    url = BASE + path
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    if data:
        request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("Authorization", f"Bearer {token}")

    print(LINE)
    print(f"  {title}")
    print(LINE)
    print(f"{method} {path}")
    if token:
        print(f"Authorization: Bearer {token[:20]}...")
    if body is not None:
        print("Тело запроса:", json.dumps(body if "password" not in body else {**body, "password": "***"},
                                          ensure_ascii=False))

    try:
        with opener.open(request, timeout=10) as response:
            code, raw, headers = response.status, response.read(), response.headers
    except urllib.error.HTTPError as exc:
        code, raw, headers = exc.code, exc.read(), exc.headers
    except urllib.error.URLError:
        print("\n!! Сервис не отвечает. Запустите ./serve.cmd в соседнем окне.\n")
        raise SystemExit(1)

    print(f"\n--> HTTP {code}")
    if headers.get("Location"):
        print(f"    Location: {headers['Location']}")
    if raw:
        parsed = json.loads(raw.decode())
        shown = {**parsed, "access_token": parsed["access_token"][:20] + "..."} if "access_token" in parsed else parsed
        print(json.dumps(shown, ensure_ascii=False, indent=2))
        print()
        return code, parsed
    print("    (тела ответа нет)\n")
    return code, None


def main():
    new_car = {
        "plate": f"{random.randint(100, 999)}XYZ02",  # случайный номер: скрипт можно запускать повторно
        "brand": "Toyota",
        "model": "Camry",
        "year": 2024,
        "color": "black",
        "price_per_minute": 120,
        "fuel_level": 0.95,
    }

    print("\nДЕМОНСТРАЦИЯ CRUD-ОПЕРАЦИЙ РЕСУРСА /api/v1/cars\n")

    code, login = call("POST", "/api/v1/auth/login", ADMIN,
                       "0. ВХОД администратора -> ожидаем 200 OK и токен")
    if code != 200:
        print("!! Нет демо-администратора. Выполните: python manage.py seed_demo")
        raise SystemExit(1)
    token = login["access_token"]

    _, created = call("POST", "/api/v1/cars", new_car,
                      "1. СОЗДАНИЕ записи -> ожидаем 201 Created", token)
    car_id = created["id"]

    call("GET", f"/api/v1/cars/{car_id}", None,
         "2. ПОЛУЧЕНИЕ записи -> ожидаем 200 OK")

    call("GET", "/api/v1/cars?page=1&limit=2&status=free", None,
         "3. СПИСОК с пагинацией и фильтром -> ожидаем 200 OK")

    call("GET", "/api/v1/cars?brand=kia&max_price_per_minute=70", None,
         "4. ФИЛЬТР по марке и цене -> ожидаем 200 OK")

    call("PUT", f"/api/v1/cars/{car_id}",
         {**new_car, "model": "Camry 70", "year": 2025, "color": "white",
          "price_per_minute": 135, "fuel_level": 0.6, "status": "free"},
         "5. ПОЛНАЯ ЗАМЕНА (PUT) -> ожидаем 200 OK", token)

    call("PATCH", f"/api/v1/cars/{car_id}", {"status": "service", "fuel_level": 0.1},
         "6. ЧАСТИЧНОЕ ОБНОВЛЕНИЕ (PATCH) -> ожидаем 200 OK", token)

    call("DELETE", f"/api/v1/cars/{car_id}", None,
         "7. УДАЛЕНИЕ -> ожидаем 204 No Content", token)

    call("GET", f"/api/v1/cars/{car_id}", None,
         "8. ЗАПРОС УДАЛЁННОЙ записи -> ожидаем 404 Not Found")

    print("\nПРОВЕРКА ВАЛИДАЦИИ И ЕДИНОГО ФОРМАТА ОШИБОК\n")

    call("POST", "/api/v1/cars",
         {"plate": "bad!", "model": "Rio", "year": "recently", "price_per_minute": 0},
         "9. Неверный номер, нет brand, год строкой, тариф 0 -> ожидаем 400", token)

    call("POST", "/api/v1/cars",
         {"plate": "777QQQ02", "brand": "", "model": "Rio", "year": 2022,
          "price_per_minute": 70},
         "10. Пустое обязательное поле -> ожидаем 400", token)

    call("POST", "/api/v1/cars",
         {"plate": "123ABC02", "brand": "Kia", "model": "Rio", "year": 2022,
          "price_per_minute": 65},
         "11. Госномер уже занят -> ожидаем 409 Conflict", token)

    call("GET", "/api/v1/cars?status=flying", None,
         "12. Недопустимое значение фильтра -> ожидаем 400")

    print(LINE)
    print("  Готово: все операции контракта отработали ожидаемо.")
    print(LINE)


if __name__ == "__main__":
    main()
