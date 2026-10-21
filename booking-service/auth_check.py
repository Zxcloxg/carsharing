"""Проверка сценариев доступа на запущенном сервисе (ПР4).

Одни и те же операции выполняются от имени анонима (без токена), клиента,
оператора и администратора; коды ответов сравниваются с ожидаемыми.
Результат печатается в консоль и сохраняется протоколом в
../docs/auth-check.md.

Перед запуском: python manage.py seed_demo (демо-пользователи всех ролей),
затем ./serve.cmd в одном окне и ./auth_check.cmd в другом.
"""
import json
import random
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

BASE = "http://127.0.0.1:8010"
API = "/api/v1"
PROTOCOL = Path(__file__).resolve().parent.parent / "docs" / "auth-check.md"

ACCOUNTS = {
    "admin": ("admin@carsharing.kz", "Steppe#Falcon2026"),
    "operator": ("operator@carsharing.kz", "Orbit#Signal2026"),
    "aigerim": ("aigerim@example.kz", "Tulip#Morning2026"),
    "daniyar": ("daniyar@example.kz", "River#Stone2026"),
}

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def request(method, path, body=None, token=None, raw_body=None, content_type="application/json"):
    data = raw_body if raw_body is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", content_type)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    try:
        with opener.open(req, timeout=10) as response:
            code, raw, headers = response.status, response.read(), response.headers
    except urllib.error.HTTPError as exc:
        code, raw, headers = exc.code, exc.read(), exc.headers
    except urllib.error.URLError:
        sys.exit("Сервис не отвечает: запустите ./serve.cmd в соседнем окне.")
    return code, (json.loads(raw) if raw else None), headers


class Protocol:
    def __init__(self):
        self.sections = []
        self.failed = 0

    def section(self, title, intro=""):
        self.sections.append({"title": title, "intro": intro, "rows": [], "samples": []})
        print(f"\n=== {title}")

    def check(self, what, actor, expected, method, path, body=None, token=None, sample=False, **kwargs):
        code, data, headers = request(method, path, body, token, **kwargs)
        ok = code == expected
        self.failed += not ok
        row = {"what": what, "actor": actor, "request": f"{method} {path}", "expected": expected, "got": code,
               "ok": ok, "error_code": (data or {}).get("error", {}).get("code") if isinstance(data, dict) else None}
        self.sections[-1]["rows"].append(row)
        # В консоли UUID сокращён до 8 символов, в протоколе путь полный.
        short = re.sub(r"([0-9a-f]{8})-[0-9a-f-]{27}", r"\1…", path)
        print(f"  [{'OK ' if ok else 'FAIL'}] {actor:<16.16} {method:<6} {short:<36} ожидалось {expected}, получено {code}"
              + (f" ({row['error_code']})" if row["error_code"] else ""))
        if sample:
            self.sections[-1]["samples"].append((f"«{what}», от имени: {actor}", code, headers, data))
        return code, data

    def markdown(self, started):
        lines = [
            "# Протокол проверки сценариев доступа (ПР4)",
            "",
            f"Дата проверки: {started:%Y-%m-%d %H:%M} (UTC). Сервис: booking-service, `{BASE}`.",
            "Запросы выполнены скриптом [`booking-service/auth_check.py`](../booking-service/auth_check.py) "
            "к запущенному сервису с реальной базой PostgreSQL; учётные записи созданы командой "
            "`python manage.py seed_demo`.",
            "",
            "Участники проверки:",
            "",
            "| От имени | Учётная запись | Роль |",
            "|---|---|---|",
            "| аноним | — (запрос без заголовка `Authorization`) | — |",
            "| клиент Айгерим | aigerim@example.kz | customer |",
            "| клиент Данияр | daniyar@example.kz | customer |",
            "| оператор | operator@carsharing.kz | operator |",
            "| администратор | admin@carsharing.kz | admin |",
            "",
            "Любую строку протокола можно повторить вручную, например сценарий 3 (curl, Git Bash):",
            "",
            "```bash",
            "B=http://127.0.0.1:8010/api/v1",
            "# вход: в ответе access_token",
            "curl -s -X POST $B/auth/login -H 'Content-Type: application/json' \\",
            "     -d '{\"email\":\"aigerim@example.kz\",\"password\":\"Tulip#Morning2026\"}'",
            "CUSTOMER=<access_token клиента>; ADMIN=<access_token администратора>",
            "curl -i -X DELETE $B/cars/<id>                                      # 401",
            "curl -i -X DELETE $B/cars/<id> -H \"Authorization: Bearer $CUSTOMER\"  # 403",
            "curl -i -X DELETE $B/cars/<id> -H \"Authorization: Bearer $ADMIN\"     # 204",
            "```",
            "",
        ]
        for index, section in enumerate(self.sections, 1):
            lines += [f"## {index}. {section['title']}", ""]
            if section["intro"]:
                lines += [section["intro"], ""]
            lines += ["| № | Проверка | От имени | Запрос | Ожидается | Получено | Код ошибки | Итог |",
                      "|---|---|---|---|---|---|---|---|"]
            for n, row in enumerate(section["rows"], 1):
                lines.append(
                    f"| {index}.{n} | {row['what']} | {row['actor']} | `{row['request']}` | {row['expected']} "
                    f"| **{row['got']}** | {('`' + row['error_code'] + '`') if row['error_code'] else '—'} "
                    f"| {'✅' if row['ok'] else '❌'} |"
                )
            lines.append("")
            for title, code, headers, data in section["samples"]:
                lines += [f"Ответ — {title}:", "", "```http", f"HTTP/1.1 {code}"]
                if headers.get("WWW-Authenticate"):
                    lines.append(f"WWW-Authenticate: {headers['WWW-Authenticate']}")
                lines.append("")
                if data is not None:
                    lines.append(json.dumps(_mask(data), ensure_ascii=False, indent=2))
                lines += ["```", ""]
        total = sum(len(s["rows"]) for s in self.sections)
        lines += [
            "## Итог",
            "",
            f"Выполнено проверок: {total}, совпало с ожидаемым: {total - self.failed}, расхождений: {self.failed}.",
            "",
            "Права реально различаются: одна и та же операция даёт 401 без токена, 403 для роли без прав "
            "(или для чужой записи) и успешный ответ для роли, которой действие разрешено.",
            "",
        ]
        return "\n".join(lines)


def _mask(data):
    if isinstance(data, dict):
        return {k: (v[:24] + "…" if k == "access_token" else _mask(v)) for k, v in data.items()}
    if isinstance(data, list):
        return [_mask(v) for v in data]
    return data


def login(name):
    email, password = ACCOUNTS[name]
    code, data, _ = request("POST", f"{API}/auth/login", {"email": email, "password": password})
    if code != 200:
        sys.exit(f"Не удалось войти как {email} (HTTP {code}). Выполните: python manage.py seed_demo")
    return data["access_token"]


def expired_token(user_id):
    """Токен с истёкшим сроком, подписанный настоящим секретом сервиса (берётся из .env)."""
    import jwt
    from dotenv import dotenv_values

    secret = dotenv_values(Path(__file__).resolve().parent / ".env")["JWT_SECRET"]
    issued = datetime.now(timezone.utc) - timedelta(hours=2)
    payload = {"sub": user_id, "role": "admin", "iss": "booking-service", "iat": issued, "exp": issued + timedelta(hours=1)}
    return jwt.encode(payload, secret, algorithm="HS256")


def forged_token(user_id):
    """Токен с ролью admin, подписанный чужим ключом — так выглядит попытка подделки."""
    import jwt

    now = datetime.now(timezone.utc)
    payload = {"sub": user_id, "role": "admin", "iss": "booking-service", "iat": now, "exp": now + timedelta(hours=1)}
    return jwt.encode(payload, "attacker-guessed-secret-attacker-guessed-secret", algorithm="HS256")


def main():
    started = datetime.now(timezone.utc)
    p = Protocol()
    tokens = {name: login(name) for name in ACCOUNTS}
    suffix = random.randint(1000, 9999)

    # --- подготовка данных -------------------------------------------------
    _, car, _ = request("POST", f"{API}/cars", {
        "plate": f"{suffix}AC02", "brand": "Kia", "model": "Rio", "year": 2023, "price_per_minute": 70,
    }, tokens["admin"])
    _, bookings, _ = request("GET", f"{API}/bookings?status=active", token=tokens["aigerim"])
    if bookings["items"]:
        booking = bookings["items"][0]
    else:
        _, free, _ = request("GET", f"{API}/cars?status=free&limit=1")
        _, booking, _ = request("POST", f"{API}/bookings", {"car_id": free["items"][0]["id"]}, tokens["aigerim"])

    # --- 1. регистрация и вход ---------------------------------------------
    p.section("Регистрация и вход", "Новый пользователь регистрируется сам; роль при регистрации всегда `customer`.")
    email, password = f"check{suffix}@example.kz", f"Tiger#Moon{suffix}"
    p.check("Регистрация", "аноним", 201, "POST", f"{API}/auth/register", {"email": email, "password": password},
            sample=True)
    p.check("Повторная регистрация того же email (в другом регистре)", "аноним", 409, "POST", f"{API}/auth/register",
            {"email": email.upper(), "password": password})
    p.check("Слишком короткий пароль", "аноним", 400, "POST", f"{API}/auth/register",
            {"email": f"x{suffix}@example.kz", "password": "123"}, sample=True)
    p.check("Некорректный email", "аноним", 400, "POST", f"{API}/auth/register",
            {"email": "not-an-email", "password": password})
    p.check("Попытка назначить себе роль admin", "аноним", 400, "POST", f"{API}/auth/register",
            {"email": f"y{suffix}@example.kz", "password": password, "role": "admin"})
    _, session = p.check("Вход с верным паролем", "новый клиент", 200, "POST", f"{API}/auth/login",
                         {"email": email, "password": password}, sample=True)
    p.check("Вход с неверным паролем", "новый клиент", 401, "POST", f"{API}/auth/login",
            {"email": email, "password": "Wrong#Pass0"}, sample=True)
    p.check("Вход с несуществующим email", "аноним", 401, "POST", f"{API}/auth/login",
            {"email": f"nobody{suffix}@example.kz", "password": password})
    newcomer = session["access_token"]
    p.check("Кто я (GET /auth/me) по выданному токену", "новый клиент", 200, "GET", f"{API}/auth/me",
            token=newcomer)

    # Новый клиент заполняет профиль и подаёт ВУ — получится заявка на проверке для сценария 3.
    _, profile, _ = request("POST", f"{API}/customers", {
        "phone": f"+7701{suffix}{random.randint(100, 999)}", "full_name": "Проверка Доступа",
        "birth_date": "1998-05-05",
    }, newcomer)
    _, license_, _ = request("POST", f"{API}/customers/{profile['id']}/driver-licenses", {
        "number": f"KZ{suffix}77", "categories": "B", "issued_on": "2020-01-10", "expires_on": "2030-01-10",
    }, newcomer)

    # --- 2. проверка токена ------------------------------------------------
    admin_id = request("GET", f"{API}/auth/me", token=tokens["admin"])[1]["id"]
    p.section("Защита маршрутов: проверка токена",
              "Маршрут изменения данных `DELETE /api/v1/cars/{id}`; во всех случаях токен невалиден — ответ 401.")
    car_path = f"{API}/cars/{car['id']}"
    p.check("Без токена", "аноним", 401, "DELETE", car_path, sample=True)
    p.check("Мусор вместо токена", "аноним", 401, "DELETE", car_path, token="not.a.jwt")
    p.check("Срок действия истёк", "администратор", 401, "DELETE", car_path, token=expired_token(admin_id),
            sample=True)
    p.check("Подпись чужим ключом (подделка role=admin)", "злоумышленник", 401, "DELETE", car_path,
            token=forged_token(admin_id))
    p.check("Публичный каталог доступен без токена", "аноним", 200, "GET", f"{API}/cars?limit=1")

    # --- 3. три сценария доступа -------------------------------------------
    p.section("Удаление машины — только администратор",
              "Одна и та же операция `DELETE /api/v1/cars/{id}` от имени четырёх участников.")
    p.check("Удалить машину", "аноним", 401, "DELETE", car_path)
    p.check("Удалить машину", "клиент Айгерим", 403, "DELETE", car_path, token=tokens["aigerim"], sample=True)
    p.check("Удалить машину", "оператор", 403, "DELETE", car_path, token=tokens["operator"])
    p.check("Удалить машину", "администратор", 204, "DELETE", car_path, token=tokens["admin"])
    p.check("Машина действительно удалена", "аноним", 404, "GET", car_path, sample=True)

    p.section("Решение по водительскому удостоверению — оператор или администратор",
              "Заявка подана новым клиентом из раздела 1; сам владелец заявки одобрить её не может.")
    license_path = f"{API}/driver-licenses/{license_['id']}"
    p.check("Одобрить ВУ", "аноним", 401, "PATCH", license_path, {"status": "approved"})
    p.check("Одобрить своё ВУ", "новый клиент", 403, "PATCH", license_path, {"status": "approved"},
            token=newcomer)
    p.check("Одобрить ВУ", "оператор", 200, "PATCH", license_path, {"status": "approved"}, token=tokens["operator"])

    p.section("Чужая бронь — владелец или персонал",
              "Бронь клиента Айгерим. Роль у Данияра та же (`customer`), но запись не его — ответ 403.")
    booking_path = f"{API}/bookings/{booking['id']}"
    p.check("Посмотреть бронь", "аноним", 401, "GET", booking_path)
    p.check("Посмотреть чужую бронь", "клиент Данияр", 403, "GET", booking_path, token=tokens["daniyar"],
            sample=True)
    p.check("Отменить чужую бронь", "клиент Данияр", 403, "PATCH", booking_path, {"status": "cancelled"},
            token=tokens["daniyar"])
    p.check("Посмотреть свою бронь", "клиент Айгерим", 200, "GET", booking_path, token=tokens["aigerim"])
    p.check("Посмотреть бронь", "оператор", 200, "GET", booking_path, token=tokens["operator"])

    p.section("Управление пользователями — только администратор")
    p.check("Список пользователей", "аноним", 401, "GET", f"{API}/users")
    p.check("Список пользователей", "клиент Айгерим", 403, "GET", f"{API}/users", token=tokens["aigerim"])
    p.check("Список пользователей", "оператор", 403, "GET", f"{API}/users", token=tokens["operator"])
    p.check("Список пользователей", "администратор", 200, "GET", f"{API}/users?limit=2", token=tokens["admin"])

    # --- 4. единый формат ошибок -------------------------------------------
    p.section("Единый формат ошибок", "Все ответы с ошибкой имеют вид `{\"error\": {\"code\", \"message\", \"details\"}}`.")
    p.check("Неизвестный URL", "аноним", 404, "GET", f"{API}/no-such-resource", sample=True)
    p.check("Неподдерживаемый метод", "администратор", 405, "PUT", f"{API}/cars", {}, token=tokens["admin"])
    p.check("Битый JSON", "администратор", 400, "POST", f"{API}/cars", token=tokens["admin"], raw_body=b"{bad json")
    p.check("Не JSON", "администратор", 415, "POST", f"{API}/cars", token=tokens["admin"], raw_body=b"plate=1",
            content_type="text/plain")

    PROTOCOL.write_text(p.markdown(started), encoding="utf-8")
    total = sum(len(s["rows"]) for s in p.sections)
    print(f"\nПроверок: {total}, расхождений: {p.failed}. Протокол: {PROTOCOL}")
    sys.exit(1 if p.failed else 0)


if __name__ == "__main__":
    main()
