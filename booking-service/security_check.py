"""Проверка типовых уязвимостей API на запущенном сервисе (ПР5).

Пункты чек-листа OWASP API Security Top 10 (2023), которые можно проверить
снаружи, реальными HTTP-запросами: ограничение частоты входа (в том числе
попытка обойти его подменой X-Forwarded-For), инъекции через параметры и
тело запроса, лишние поля в ответах, права на уровне полей, коды 401/403 на
всех защищённых маршрутах и конфигурация сервиса.

Перед запуском: python manage.py seed_demo, затем ./serve.cmd в одном окне и
./security_check.cmd в другом. Сервис нужно перезапустить перед повторным
прогоном: раздел 1 исчерпывает лимит попыток входа с этого IP.

Протокол сохраняется в ../docs/security-check.md (или в файл из --out).
"""
import argparse
import json
import random
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "http://127.0.0.1:8010"
API = "/api/v1"
DEFAULT_PROTOCOL = Path(__file__).resolve().parent.parent / "docs" / "security-check.md"
FAKE_ID = "00000000-0000-4000-8000-000000000000"
LOGIN_LIMIT = 10  # LOGIN_THROTTLE_RATE по умолчанию: 10 попыток в минуту с одного IP
SENSITIVE_KEY = re.compile(r"pass|hash|secret|salt", re.IGNORECASE)

ACCOUNTS = {
    "admin": ("admin@carsharing.kz", "Steppe#Falcon2026"),
    "operator": ("operator@carsharing.kz", "Orbit#Signal2026"),
    "aigerim": ("aigerim@example.kz", "Tulip#Morning2026"),
}

LEVELS = {"auth": "нужен вход", "staff": "только персонал", "admin": "только администратор"}

# Все защищённые маршруты API и минимальная роль: auth — любой вход, staff — operator/admin, admin — только admin.
PROTECTED_ROUTES = [
    ("GET", "/auth/me", "auth"),
    ("GET", "/users", "admin"),
    ("GET", "/users/{id}", "admin"),
    ("PATCH", "/users/{id}", "admin"),
    ("POST", "/cars", "admin"),
    ("PUT", "/cars/{id}", "admin"),
    ("PATCH", "/cars/{id}", "staff"),
    ("DELETE", "/cars/{id}", "admin"),
    ("GET", "/cars/{id}/status-history", "staff"),
    ("GET", "/customers", "staff"),
    ("POST", "/customers", "auth"),
    ("GET", "/customers/{id}", "auth"),
    ("PATCH", "/customers/{id}", "staff"),
    ("GET", "/customers/{id}/driver-licenses", "auth"),
    ("POST", "/customers/{id}/driver-licenses", "auth"),
    ("GET", "/driver-licenses/{id}", "auth"),
    ("PATCH", "/driver-licenses/{id}", "staff"),
    ("GET", "/bookings", "auth"),
    ("POST", "/bookings", "auth"),
    ("GET", "/bookings/{id}", "auth"),
    ("PATCH", "/bookings/{id}", "auth"),
    ("GET", "/bookings/{id}/payments", "auth"),
    ("POST", "/bookings/{id}/payments", "auth"),
    ("GET", "/payments/{id}", "auth"),
    ("GET", "/tasks", "staff"),
    ("GET", "/tasks/{id}", "staff"),
]

opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


class Reply:
    def __init__(self, code, raw, headers):
        self.code, self.raw, self.headers = code, raw, headers
        try:
            self.data = json.loads(raw) if raw else None
        except ValueError:
            self.data = None

    @property
    def error_code(self):
        if isinstance(self.data, dict) and isinstance(self.data.get("error"), dict):
            return self.data["error"].get("code")
        return None


def request(method, path, body=None, token=None, headers=None, raw_body=None):
    data = raw_body if raw_body is not None else (json.dumps(body).encode() if body is not None else None)
    req = urllib.request.Request(BASE + path, data=data, method=method)
    if data is not None:
        req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    for name, value in (headers or {}).items():
        req.add_header(name, value)
    try:
        with opener.open(req, timeout=15) as response:
            return Reply(response.status, response.read(), response.headers)
    except urllib.error.HTTPError as exc:
        return Reply(exc.code, exc.read(), exc.headers)
    except urllib.error.URLError:
        sys.exit("Сервис не отвечает: запустите ./serve.cmd в соседнем окне.")


def sensitive_keys(data, prefix=""):
    """Ключи ответа (на любой глубине), похожие на пароль, хэш или секрет."""
    found = []
    if isinstance(data, dict):
        for key, value in data.items():
            path = f"{prefix}.{key}" if prefix else key
            if SENSITIVE_KEY.search(key):
                found.append(path)
            found += sensitive_keys(value, path)
    elif isinstance(data, list):
        for item in data:
            found += sensitive_keys(item, prefix + "[]")
    return found


class Protocol:
    def __init__(self):
        self.sections = []
        self.failed = 0

    def section(self, title, intro=""):
        self.sections.append({"title": title, "intro": intro, "rows": [], "samples": []})
        print(f"\n=== {title}")

    def row(self, what, actor, request_text, expected, got, ok, error_code=None):
        self.failed += not ok
        self.sections[-1]["rows"].append({
            "what": what, "actor": actor, "request": request_text, "expected": expected, "got": got,
            "ok": ok, "error_code": error_code,
        })
        short = re.sub(r"([0-9a-f]{8})-[0-9a-f-]{27}", r"\1…", request_text)
        print(f"  [{'OK ' if ok else 'FAIL'}] {actor:<14.14} {short:<46.46} ожидалось {expected}, получено {got}")

    def check(self, what, actor, expected, method, path, body=None, token=None, headers=None, raw_body=None,
              verify=None, sample=False):
        """Запрос и сравнение с ожидаемым. verify(reply) -> (ok, описание) — для проверок сверх кода ответа."""
        reply = request(method, path, body, token, headers, raw_body)
        if verify:
            ok, got = verify(reply)
        else:
            ok, got = reply.code == expected, str(reply.code)
        self.row(what, actor, f"{method} {urllib.parse.unquote(path)}", expected, got, ok, reply.error_code)
        if sample:
            self.sections[-1]["samples"].append((what, reply))
        return reply

    def markdown(self, started, label):
        lines = [
            "# Протокол проверки безопасности API (ПР5)" + (f" — {label}" if label else ""),
            "",
            f"Дата проверки: {started:%Y-%m-%d %H:%M} (UTC). Сервис: booking-service, `{BASE}`.",
            "Запросы выполнены скриптом [`booking-service/security_check.py`](../booking-service/security_check.py) "
            "к запущенному сервису с реальной базой PostgreSQL (демо-данные `python manage.py seed_demo`). "
            "Чек-лист с выводами и записью «до/после» — в [`security.md`](security.md).",
            "",
        ]
        for index, section in enumerate(self.sections, 1):
            lines += [f"## {index}. {section['title']}", ""]
            if section["intro"]:
                lines += [section["intro"], ""]
            lines += ["| № | Проверка | От имени | Запрос | Ожидается | Получено | Код ошибки | Итог |",
                      "|---|---|---|---|---|---|---|---|"]
            for n, row in enumerate(section["rows"], 1):
                request_text = row["request"].replace("|", "\\|")
                lines.append(
                    f"| {index}.{n} | {row['what']} | {row['actor']} | `{request_text}` | {row['expected']} "
                    f"| **{row['got']}** | {('`' + row['error_code'] + '`') if row['error_code'] else '—'} "
                    f"| {'✅' if row['ok'] else '❌'} |"
                )
            lines.append("")
            for title, reply in section["samples"]:
                lines += [f"Ответ — «{title}»:", "", "```http", f"HTTP/1.1 {reply.code}"]
                for header in ("Content-Type", "Retry-After", "WWW-Authenticate", "X-Content-Type-Options",
                               "X-Frame-Options", "Referrer-Policy", "Cross-Origin-Opener-Policy"):
                    if reply.headers.get(header):
                        lines.append(f"{header}: {reply.headers[header]}")
                lines.append("")
                if reply.data is not None:
                    lines.append(json.dumps(_mask(reply.data), ensure_ascii=False, indent=2))
                else:
                    text = _visible_text(reply.raw.decode("utf-8", "replace"))
                    lines.append("(видимый текст HTML-страницы)")
                    lines.append(text[:700] + (" … (обрезано)" if len(text) > 700 else ""))
                lines += ["```", ""]
        total = sum(len(s["rows"]) for s in self.sections)
        lines += [
            "## Итог",
            "",
            f"Выполнено проверок: {total}, совпало с ожидаемым: {total - self.failed}, расхождений: {self.failed}.",
            "",
        ]
        return "\n".join(lines)


def _visible_text(html):
    """Текст страницы без стилей, скриптов и тегов — то, что увидит человек в браузере."""
    html = re.sub(r"(?is)<(style|script)\b.*?</\1>", " ", html)
    return re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", html)).strip()


def _mask(data):
    if isinstance(data, dict):
        return {k: (v[:24] + "…" if k == "access_token" else _mask(v)) for k, v in data.items()}
    if isinstance(data, list):
        return [_mask(v) for v in data[:3]] + (["…"] if len(data) > 3 else [])
    return data


def login(name):
    email, password = ACCOUNTS[name]
    reply = request("POST", f"{API}/auth/login", {"email": email, "password": password})
    if reply.code != 200:
        sys.exit(f"Не удалось войти как {email} (HTTP {reply.code}). Выполните seed_demo и перезапустите сервис.")
    return reply.data["access_token"]


# --- разделы проверки ---------------------------------------------------------
def check_rate_limit(p):
    p.section(
        "Ограничение частоты запросов (API4:2023, API2:2023)",
        f"Лимит — {LOGIN_LIMIT} попыток входа и {LOGIN_LIMIT} регистраций в минуту с одного IP. Злоумышленник "
        "перебирает пароль администратора и в каждом запросе подставляет новый заголовок `X-Forwarded-For`, "
        "выдавая себя за другой IP. Все запросы на самом деле идут с одного адреса 127.0.0.1, поэтому после "
        f"{LOGIN_LIMIT} попыток сервис обязан отвечать 429.",
    )
    email = ACCOUNTS["admin"][0]
    codes = []
    for attempt in range(1, LOGIN_LIMIT + 3):
        reply = request("POST", f"{API}/auth/login", {"email": email, "password": f"Guess#{attempt:04d}"},
                        headers={"X-Forwarded-For": f"203.0.113.{attempt}"})
        codes.append(reply.code)
        last = reply
    first, rest = codes[:LOGIN_LIMIT], codes[LOGIN_LIMIT:]
    p.row(f"Попытки 1–{LOGIN_LIMIT}: неверный пароль, каждый раз новый X-Forwarded-For", "злоумышленник",
          f"POST {API}/auth/login", f"401 ×{LOGIN_LIMIT}", _summary(first), all(c == 401 for c in first),
          "invalid_credentials")
    for n, code in enumerate(rest, LOGIN_LIMIT + 1):
        p.row(f"Попытка {n}: снова подменён X-Forwarded-For", "злоумышленник", f"POST {API}/auth/login",
              "429", str(code), code == 429, "throttled" if code == 429 else "invalid_credentials")
    p.sections[-1]["samples"].append((f"попытка {LOGIN_LIMIT + 2}", last))

    codes = [request("POST", f"{API}/auth/register", {}).code for _ in range(LOGIN_LIMIT)]
    p.row(f"Регистрация: {LOGIN_LIMIT} запросов подряд (пустое тело)", "аноним", f"POST {API}/auth/register",
          f"400 ×{LOGIN_LIMIT}", _summary(codes), all(c == 400 for c in codes), "validation_error")
    p.check(f"Регистрация: запрос {LOGIN_LIMIT + 1}", "аноним", 429, "POST", f"{API}/auth/register", {},
            sample=True)


def _summary(codes):
    groups = []
    for code in codes:
        if groups and groups[-1][0] == code:
            groups[-1][1] += 1
        else:
            groups.append([code, 1])
    return ", ".join(f"{code} ×{count}" for code, count in groups)


def wait_for_login_window():
    """Попытки из раздела 1 могли исчерпать лимит входа с 127.0.0.1 — ждём, пока окно в минуту сдвинется."""
    print("\n... ожидание 61 с: окно ограничения частоты входа")
    time.sleep(61)


def check_injection(p, admin):
    p.section(
        "Инъекции (API8:2023, OWASP Injection)",
        "Значения из запроса попадают в SQL только параметрами ORM Django, поэтому SQL-фрагмент — просто строка: "
        "по ней ищется марка, она не исполняется. Ответ — штатный код, не 500.",
    )
    total_before = request("GET", f"{API}/cars?limit=1").data["total"]

    def empty_list(reply):
        total = (reply.data or {}).get("total")
        return reply.code == 200 and total == 0, f"{reply.code}, найдено {total}"

    for payload in ("' OR '1'='1", "Kia'; DROP TABLE cars; --", "%' UNION SELECT password_hash FROM users --"):
        p.check("SQL-фрагмент в фильтре `brand`", "аноним", "200, найдено 0", "GET",
                f"{API}/cars?brand={urllib.parse.quote(payload)}", verify=empty_list)
    p.check("SQL-фрагмент в числовом фильтре", "аноним", 400, "GET",
            f"{API}/cars?max_price_per_minute={urllib.parse.quote('1 OR 1=1')}")
    p.check("SQL-фрагмент вместо id в пути", "аноним", 404, "GET", f"{API}/cars/{urllib.parse.quote(chr(39) + ' OR 1=1 --')}")
    p.check("SQL-фрагмент в email при входе", "аноним", 400, "POST", f"{API}/auth/login",
            {"email": "admin@carsharing.kz' --", "password": "x"})
    p.check("SQL-фрагмент в пароле при входе", "аноним", 401, "POST", f"{API}/auth/login",
            {"email": ACCOUNTS["admin"][0], "password": "' OR '1'='1"})
    p.check("SQL-фрагмент в текстовом поле сохраняется как текст", "администратор", 201, "POST", f"{API}/cars",
            {"plate": f"{random.randint(1000, 9999)}SQL", "brand": "Robert'); DROP TABLE cars;--", "model": "X",
             "year": 2024, "price_per_minute": 50}, token=admin, sample=True)

    def intact(reply):
        total = (reply.data or {}).get("total")
        return reply.code == 200 and total == total_before + 1, f"{reply.code}, машин {total}"

    p.check("Таблица cars цела: машин стало на одну больше", "аноним", f"200, машин {total_before + 1}", "GET",
            f"{API}/cars?limit=1", verify=intact)
    # Убираем машину с «опасной» маркой, чтобы не оставлять её в демо-данных.
    created = request("GET", f"{API}/cars?brand={urllib.parse.quote('Robert')}&limit=100").data["items"]
    for car in created:
        request("DELETE", f"{API}/cars/{car['id']}", token=admin)


def check_data_exposure(p, tokens):
    p.section(
        "Лишние данные в ответах (API3:2023 — Excessive Data Exposure)",
        "Тело ответа обходится рекурсивно; ни на какой глубине не должно быть ключей, похожих на пароль, хэш, "
        "соль или секрет.",
    )

    def clean(reply):
        leaked = sensitive_keys(reply.data)
        keys = sorted(_top_keys(reply.data))
        ok = reply.code == 200 and not leaked
        return ok, f"{reply.code}; " + (f"утечка: {', '.join(leaked)}" if leaked else f"поля: {', '.join(keys)}")

    p.check("Вход: в ответе токен и учётная запись", "администратор", "200, без пароля и хэша", "POST",
            f"{API}/auth/login", {"email": ACCOUNTS["admin"][0], "password": ACCOUNTS["admin"][1]}, verify=clean)
    p.check("Текущий пользователь", "клиент Айгерим", "200, без пароля и хэша", "GET", f"{API}/auth/me",
            token=tokens["aigerim"], verify=clean, sample=True)
    p.check("Список всех учётных записей", "администратор", "200, без пароля и хэша", "GET",
            f"{API}/users?limit=100", token=tokens["admin"], verify=clean)
    me = request("GET", f"{API}/auth/me", token=tokens["aigerim"]).data
    p.check("Свой профиль клиента", "клиент Айгерим", "200, без пароля и хэша", "GET",
            f"{API}/customers/{me['customer_id']}", token=tokens["aigerim"], verify=clean)
    p.check("Публичный каталог машин", "аноним", "200, без пароля и хэша", "GET", f"{API}/cars?limit=100",
            verify=clean)


def _top_keys(data):
    if isinstance(data, dict):
        if "items" in data and isinstance(data["items"], list) and data["items"]:
            return {f"items[].{k}" for k in data["items"][0]} | (set(data) - {"items"})
        return set(data)
    return set()


def check_property_level(p, tokens):
    p.section(
        "Права на уровне полей (API3:2023 — BOPLA, API5:2023 — BFLA)",
        "По матрице ролей (docs/auth.md) оператор меняет у машины только статус и уровень топлива; тариф, "
        "госномер, марку и год меняет администратор. Роль при регистрации не выбирается, клиент не может сам "
        "снять с себя блокировку.",
    )
    plate = f"{random.randint(1000, 9999)}SEC"
    car = request("POST", f"{API}/cars", {"plate": plate, "brand": "Kia", "model": "Rio", "year": 2023,
                                          "price_per_minute": 70}, tokens["admin"]).data
    path = f"{API}/cars/{car['id']}"
    p.check("Оператор переводит машину в сервис (разрешено)", "оператор", 200, "PATCH", path,
            {"status": "service"}, tokens["operator"])
    p.check("Оператор обновляет уровень топлива (разрешено)", "оператор", 200, "PATCH", path,
            {"fuel_level": 0.4}, tokens["operator"])
    p.check("Оператор снижает тариф до 1 ₸/мин", "оператор", 403, "PATCH", path, {"price_per_minute": 1},
            tokens["operator"], sample=True)
    p.check("Оператор меняет госномер", "оператор", 403, "PATCH", path, {"plate": "HACK01"}, tokens["operator"])

    def price_kept(reply):
        price = (reply.data or {}).get("price_per_minute")
        return reply.code == 200 and float(price) == 70, f"{reply.code}, тариф {price}"

    p.check("Тариф машины после попыток оператора", "аноним", "200, тариф 70", "GET", path, verify=price_kept)
    p.check("Администратор меняет тариф (разрешено)", "администратор", 200, "PATCH", path,
            {"price_per_minute": 75}, tokens["admin"])
    me = request("GET", f"{API}/auth/me", token=tokens["aigerim"]).data
    p.check("Клиент меняет статус своего профиля", "клиент Айгерим", 403, "PATCH",
            f"{API}/customers/{me['customer_id']}", {"status": "active"}, tokens["aigerim"])
    request("DELETE", path, token=tokens["admin"])


def check_route_protection(p, tokens):
    p.section(
        "Коды 401/403 на всех защищённых маршрутах (API2:2023, API5:2023)",
        "Перебираются все защищённые маршруты API. Без токена — 401 (`not_authenticated`); клиенту на маршруты "
        "персонала и оператору на маршруты администратора — 403 (`permission_denied`). В пути — несуществующий "
        "id: права проверяются раньше, чем поиск записи, поэтому по коду ответа нельзя узнать, есть ли запись.",
    )
    for method, route, level in PROTECTED_ROUTES:
        path = API + route.replace("{id}", FAKE_ID)
        body = {} if method in ("POST", "PUT", "PATCH") else None
        p.check(LEVELS[level], "аноним", 401, method, path, body)
    for method, route, level in PROTECTED_ROUTES:
        if level in ("staff", "admin"):
            path = API + route.replace("{id}", FAKE_ID)
            p.check(LEVELS[level], "клиент Айгерим", 403, method, path, {} if method != "GET" else None, tokens["aigerim"])
    for method, route, level in PROTECTED_ROUTES:
        if level == "admin":
            path = API + route.replace("{id}", FAKE_ID)
            p.check(LEVELS[level], "оператор", 403, method, path, {} if method != "GET" else None, tokens["operator"])
    # Публичный маршрут входа, но в запросе есть действующий токен (например, клиент перелогинивается).
    p.check("Вход с неверным паролем, в заголовке — токен клиента", "клиент Айгерим", 401, "POST",
            f"{API}/auth/login", {"email": ACCOUNTS["admin"][0], "password": "Wrong#Pass0"}, tokens["aigerim"],
            sample=True)


def check_configuration(p):
    p.section(
        "Конфигурация сервиса (API8:2023 — Security Misconfiguration)",
        "Режим отладки выключен: на неизвестный адрес сервис не показывает отладочную страницу со списком "
        "маршрутов. Ответы API несут защитные заголовки, сбои не раскрывают стектрейс, размер страницы ограничен.",
    )

    def no_debug_page(reply):
        body = reply.raw.decode("utf-8", "replace")
        debug = "URLconf" in body or "DEBUG = True" in body
        return reply.code == 404 and not debug, f"{reply.code}, " + ("отладочная страница Django" if debug else "без подробностей")

    p.check("Неизвестный адрес вне API", "аноним", "404 без подробностей", "GET", "/no-such-page",
            verify=no_debug_page, sample=True)

    def headers_ok(reply):
        expected = {"X-Content-Type-Options": "nosniff", "X-Frame-Options": "DENY"}
        missing = [h for h, v in expected.items() if reply.headers.get(h) != v]
        return reply.code == 200 and not missing, f"{reply.code}, " + (f"нет: {', '.join(missing)}" if missing else "nosniff, DENY")

    p.check("Защитные заголовки ответа", "аноним", "nosniff, DENY", "GET", f"{API}/cars?limit=1", verify=headers_ok,
            sample=True)

    def no_traceback(reply):
        leak = b"Traceback" in reply.raw or b"File \"" in reply.raw
        return reply.code == 400 and not leak, f"{reply.code}, " + ("стектрейс в ответе" if leak else "без стектрейса")

    p.check("Битый JSON в теле", "аноним", "400 без стектрейса", "POST", f"{API}/auth/login",
            raw_body=b'{"email": ', verify=no_traceback)
    p.check("Слишком большая страница списка (limit=1000)", "аноним", 400, "GET", f"{API}/cars?limit=1000")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", type=Path, default=DEFAULT_PROTOCOL, help="куда сохранить протокол")
    parser.add_argument("--label", default="", help="подпись к заголовку протокола, например «до исправлений»")
    args = parser.parse_args()

    started = datetime.now(timezone.utc)
    p = Protocol()
    check_rate_limit(p)
    wait_for_login_window()
    tokens = {name: login(name) for name in ACCOUNTS}
    check_injection(p, tokens["admin"])
    check_data_exposure(p, tokens)
    check_property_level(p, tokens)
    check_route_protection(p, tokens)
    check_configuration(p)

    args.out.write_text(p.markdown(started, args.label), encoding="utf-8")
    total = sum(len(s["rows"]) for s in p.sections)
    print(f"\nПроверок: {total}, расхождений: {p.failed}. Протокол: {args.out}")
    sys.exit(1 if p.failed else 0)


if __name__ == "__main__":
    main()
