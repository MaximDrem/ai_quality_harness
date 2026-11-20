"""Профиль пользователя («кто я») — порт `user-service.ts` из desktop-ai-app.

Что перенесено из desktop (референс, не импорт):

  * `packages/desktop-electron/src/main/services/user-service.ts` —
    двухшаговый поиск: GET `current/person` (табельный номер) и POST
    `search-engine/find` (ФИО, руководитель, подразделение, статус), маппинг
    в текст «Информация о пользователе: …», кэш и дедупликация параллельных
    запросов;
  * `packages/desktop-electron/src/main/utils/env.ts:193-194` — адреса
    `CURRENT_PERSON_URL` / `CORP_SEARCH_URL` (у нас — свойства
    `ClientConfig.current_person_url` / `corp_search_url`);
  * `apiClient.ts` (`documentNavigation: true`) — для `current/person`
    браузерные заголовки навигации (`Sec-Fetch-*`, `Upgrade-Insecure-Requests`)
    и НИКАКИХ `Origin`/`Referer`.

Импортировать TS-код из desktop-ai-app нельзя (другой рантайм), поэтому
здесь Python-реализация тех же контрактов. Единственное переиспользование
кода проекта — `normalize_cookies` cookie-прокси (разбор
`GIGACHAT_SESSION_COOKIES`), чтобы не дублировать нормализацию.

Модуль fail-soft: любая ошибка -> `None` + строка в stderr. Инстанс агента
стартует как раньше, просто без блока `User:` в системном промпте.

Ручная проверка: `python -m client.auth.user_info --print`.
"""
from __future__ import annotations

import argparse
import json
import os
import ssl
import sys
import threading
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from ..core.config import ClientConfig
from ..core.errors import ClientError

# Ответы upstream небольшие, но с запасом (как _MAX_BODY в sudir.py).
_MAX_BODY = 400_000

# Таймаут одного запроса профиля (как timeout у sudir-логина).
DEFAULT_TIMEOUT = 30.0

# Accept из user-service.ts: запрос к current/person эмулирует навигацию
# браузера по документу.
_BROWSER_ACCEPT = (
    "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,"
    "image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7"
)

# Реальный User-Agent браузера: desktop берёт его из заголовков, снятых при
# Sudir-логине (`sessionService.getBrowserHeaders()`), у нас окно логина
# отсутствует — поэтому дефолт Chrome и override через env для стендов,
# которые фильтруют UA.
_DEFAULT_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_BROWSER_UA_ENV = "GIGATOOL_BROWSER_UA"

# Порядок проверки периодов в одном элементе `status` (как в statusText:
# workRemote ?? isAbsent ?? willAbsent ?? isLongAbsent ?? isVacation ??
# businessTrip ?? mixedWork).
_STATUS_KEYS = (
    "workRemote",
    "isAbsent",
    "willAbsent",
    "isLongAbsent",
    "isVacation",
    "businessTrip",
    "mixedWork",
)


@dataclass
class UserInfo:
    """Профиль сотрудника (как `UserInfo` в user-service.ts).

    Все поля, кроме `description`, необязательны: контракт `find`/`person` не
    гарантирует стабильного набора, отсутствие поля не должно ломать старт.
    """

    # Готовая строка для инъекции в системный промпт sidecar'а.
    description: str
    family_name: str = ""
    given_name: str = ""
    patronymic: str = ""
    boss_name_info: str = ""
    uid: str = ""
    address: str = ""
    email_sigma: str = ""
    position: str = ""
    phone_mobile: str = ""
    user_time_zone: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------- primitives
def short_date(value: Optional[str]) -> str:
    """ISO-дата/дата-время -> `YYYY-MM-DD` (как `shortDate` в user-service.ts)."""
    if not value:
        return ""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return ""
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone()
    return f"{parsed.year:04d}-{parsed.month:02d}-{parsed.day:02d}"


def format_range(label: str, start: Optional[str], end: Optional[str]) -> Optional[str]:
    """Период в человекочитаемом виде (как `formatRange` в user-service.ts)."""
    if not start and not end:
        return None
    s = short_date(start)
    e = short_date(end)
    if s and e:
        return f"{label} ({s} – {e})"
    if e:
        return f"{label} до {e}"
    if s:
        return f"{label} с {s}"
    return label


def _first_present(mapping: Dict[str, Any], *keys: str) -> Any:
    """Аналог цепочки `a ?? b ?? c` (None/отсутствие, но не «пусто»)."""
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return value
    return None


def _period(value: Any) -> Optional[Tuple[str, str]]:
    """`{start, end}` -> (start, end) или None, если периода нет."""
    if not isinstance(value, dict):
        return None
    start = value.get("start") or ""
    end = value.get("end") or ""
    if not start and not end:
        return None
    return str(start), str(end)


def status_text(data: Dict[str, Any]) -> str:
    """Человекочитаемый текущий статус сотрудника.

    Дословный перенос `statusText` из user-service.ts: приоритет
    mixedWork -> isVacation -> businessTrip -> workRemote ->
    isAbsent/willAbsent/isLongAbsent -> отпуск по датам -> удалёнка ->
    «в офисе».
    """
    entries = data.get("status")
    if isinstance(entries, list):
        for item in entries:
            if not isinstance(item, dict):
                continue
            # `find` в TS берёт ПЕРВЫЙ определённый ключ элемента и проверяет у
            # него start/end; если периода нет — элемент не подходит.
            raw = _first_present(item, *_STATUS_KEYS)
            period = _period(raw)
            if period is None:
                continue

            mixed = item.get("mixedWork")
            if isinstance(mixed, dict):
                in_office = mixed.get("inOffice")
                days = ""
                if isinstance(in_office, list) and in_office:
                    days = f" (офис: {', '.join(str(d) for d in in_office)})"
                return f"гибридный график{days}"

            candidates = (
                ("в отпуске", _period(item.get("isVacation"))),
                ("в командировке", _period(item.get("businessTrip"))),
                ("работает удалённо", _period(item.get("workRemote"))),
                (
                    "отсутствует",
                    _period(_first_present(item, "isAbsent", "willAbsent", "isLongAbsent")),
                ),
            )
            for label, span in candidates:
                if span is None:
                    continue
                result = format_range(label, span[0], span[1])
                if result:
                    return result

    vacation = format_range(
        "в отпуске",
        data.get("userVacationStart"),
        data.get("userVacationEnd"),
    )
    if vacation:
        return vacation
    distant = format_range(
        "работает удалённо",
        data.get("distantWorkStart"),
        data.get("distantWorkEnd") or "",
    )
    if distant:
        return distant
    return "в офисе"


def format_user_info_summary(contact: Dict[str, Any]) -> str:
    """Строка «Информация о пользователе: …» для инъекции в промпт.

    Дословный перенос `formatUserInfoSummary` из user-service.ts.
    """
    d = contact.get("data") if isinstance(contact.get("data"), dict) else {}
    name = " ".join(
        str(part) for part in (d.get("familyName"), d.get("givenName"), d.get("patronymic")) if part
    )

    departments = d.get("department") if isinstance(d.get("department"), list) else []
    department = departments[0] if departments and isinstance(departments[0], dict) else None
    teams = d.get("agile") if isinstance(d.get("agile"), list) else []
    team = teams[0] if teams and isinstance(teams[0], dict) else None

    tz = d.get("userTimeZone")
    parts: List[str] = [
        f"Меня зовут {name}",
        f"Руководитель: {d.get('bossNameInfo') if d.get('bossNameInfo') is not None else '-'}",
        f"Табельный номер: {contact.get('uid') if contact.get('uid') is not None else '-'}",
        f"Рабочее место: {d.get('address') if d.get('address') is not None else '-'}",
        f"Почта: {_first_present(d, 'emailSigma', 'emailInternal') or '-'}",
        f"Должность: {d.get('position') if d.get('position') is not None else '-'}",
        f"Номер телефона: {d.get('phoneMobile') if d.get('phoneMobile') is not None else '-'}",
        f"Часовой пояс: {tz if tz is not None else '-'}",
    ]

    if d.get("birthday"):
        parts.append(f"День рождения: {short_date(d.get('birthday'))}")
    if department and (department.get("chain") or department.get("deptName")):
        parts.append(
            f"Подразделение: {department.get('chain') or department.get('deptName')}"
        )
    elif d.get("structure") or d.get("shortDepartName"):
        parts.append(f"Подразделение: {d.get('structure') or d.get('shortDepartName')}")
    if team and team.get("deptName"):
        role = _first_present(d, "agileRoleName") or team.get("role")
        parts.append(f"Команда: {team.get('deptName')}{f' ({role})' if role else ''}")
    if d.get("phoneInner"):
        parts.append(f"Внутренний телефон: {d.get('phoneInner')}")
    if d.get("userHireDate"):
        parts.append(f"Дата найма: {short_date(d.get('userHireDate'))}")
    parts.append(f"Статус: {status_text(d)}")

    return f"Информация о пользователе: {'; '.join(parts)}."


# ------------------------------------------------------------------- service
def _cookie_header(cfg: ClientConfig) -> str:
    """Cookie-строка из конфига/окружения (разбор — общий с прокси)."""
    from ..server.proxy.cookies import normalize_cookies

    return normalize_cookies(os.environ.get("GIGACHAT_SESSION_COOKIES", ""), cfg.session_cookies or {})


def _browser_user_agent() -> str:
    return os.environ.get(_BROWSER_UA_ENV, "").strip() or _DEFAULT_BROWSER_UA


class UserInfoService:
    """Профиль текущего сотрудника: запрос, кэш и дедупликация вызовов.

    Аналог singleton `userService` в desktop: результат запрашивается один раз
    и переиспользуется; параллельные вызовы ждут один запрос, а не стартуют
    несколько.
    """

    def __init__(self, cfg: ClientConfig, timeout: float = DEFAULT_TIMEOUT):
        self.cfg = cfg
        self.timeout = timeout
        self._info: Optional[UserInfo] = None
        self._lock = threading.Lock()
        self._in_flight: Optional[threading.Event] = None

    @property
    def current(self) -> Optional[UserInfo]:
        """Уже полученный профиль без сетевого запроса (аналог currentUserInfo)."""
        return self._info

    # ------------------------------------------------------------ http layer
    def _ssl_context(self) -> Optional[ssl.SSLContext]:
        if not self.cfg.insecure_tls:
            return None
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _open(
        self,
        method: str,
        url: str,
        headers: Dict[str, str],
        payload: Optional[Dict[str, Any]] = None,
    ) -> Tuple[int, Optional[Any]]:
        body = None
        if payload is not None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(url, data=body, method=method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout, context=self._ssl_context()) as resp:
                raw = resp.read(_MAX_BODY)
                status = resp.status
        except urllib.error.HTTPError as err:
            raw = err.read(_MAX_BODY) or b""
            status = err.code
        parsed: Optional[Any] = None
        if raw:
            try:
                parsed = json.loads(raw.decode("utf-8", "replace"))
            except ValueError:
                parsed = None
        return status, parsed

    def _navigation_headers(self) -> Dict[str, str]:
        """Заголовки top-level навигации браузера (apiClient documentNavigation).

        `Origin`/`Referer` для навигации НЕ отправляются — так же, как в
        desktop: fetchCurrentPerson вызывает appFetch с
        `documentNavigation: true`, который их явно вырезает.
        """
        headers = {
            "User-Agent": _browser_user_agent(),
            "Accept": _BROWSER_ACCEPT,
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Cache-Control": "no-cache",
            "Pragma": "no-cache",
            "Priority": "u=0, i",
            "Sec-Fetch-Dest": "document",
            "Sec-Fetch-Mode": "navigate",
            "Sec-Fetch-Site": "none",
            "Sec-Fetch-User": "?1",
            "Upgrade-Insecure-Requests": "1",
        }
        cookie = _cookie_header(self.cfg)
        if cookie:
            headers["Cookie"] = cookie
        return headers

    # --------------------------------------------------------------- lookups
    def fetch_current_person(self) -> Dict[str, str]:
        """GET current/person -> {"tabNum", "userId"} (как fetchCurrentPerson)."""
        status, data = self._open("GET", self.cfg.current_person_url, self._navigation_headers())
        if not 200 <= status < 300:
            raise ClientError(f"current/person вернул HTTP {status}")

        # Контракт: {success, body: [...]}; допускаем и голый массив (как в TS).
        body: Any = data.get("body") if isinstance(data, dict) else data
        person = body[0] if isinstance(body, list) and body and isinstance(body[0], dict) else None
        tab_num = person.get("tabNum") if person else None
        if not tab_num:
            raise ClientError("current/person не вернул tabNum")
        return {
            "tabNum": str(tab_num),
            "userId": str(person.get("userId") or tab_num),
        }

    def search_contact(self, tab_num: str) -> Optional[Dict[str, Any]]:
        """POST search-engine/find -> контакт с блоком `data` (или None)."""
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": _browser_user_agent(),
            # Не-навигационный запрос: браузерные Origin/Referer, как их
            # подставляет apiClient из заголовков логина.
            "Origin": self.cfg.shared_api_origin,
            "Referer": f"{self.cfg.shared_api_origin}/",
        }
        cookie = _cookie_header(self.cfg)
        if cookie:
            headers["Cookie"] = cookie
        payload = {
            "categories": ["CONTACTS"],
            "platformType": "WEB",
            "primary": False,
            "spellCorrection": False,
            "contacts": {"tabnum": tab_num},
            "searchEverywhere": False,
            "pagination": {"start": 0, "count": 10},
        }
        status, data = self._open("POST", self.cfg.corp_search_url, headers, payload)
        if not 200 <= status < 300:
            raise ClientError(f"search-engine/find вернул HTTP {status}")

        items = data.get("data") if isinstance(data, dict) else None
        contact = items[0] if isinstance(items, list) and items else None
        # Пустой поиск или контакт без data — «нет расширенных данных», а не
        # ошибка: вызывающий откатится на табельный номер.
        if isinstance(contact, dict) and isinstance(contact.get("data"), dict) and contact["data"]:
            return contact
        return None

    def _perform_fetch(self) -> Optional[UserInfo]:
        try:
            person = self.fetch_current_person()
        except Exception as err:  # noqa: BLE001 — профиль не должен ломать старт
            print(f"[user-info] профиль недоступен: {err}", file=sys.stderr)
            return None

        # Fallback как в desktop: данных поиска нет — сообщаем табельный номер.
        info = UserInfo(description=f"Табельный номер: {person['tabNum']}", uid=person["tabNum"])
        try:
            contact = self.search_contact(person["tabNum"])
            if contact is None:
                raise ClientError("contact info is empty")
            d = contact.get("data") or {}
            info = UserInfo(
                description=format_user_info_summary(contact),
                family_name=str(d.get("familyName") or ""),
                given_name=str(d.get("givenName") or ""),
                patronymic=str(d.get("patronymic") or ""),
                boss_name_info=str(d.get("bossNameInfo") or ""),
                uid=str(contact.get("uid") or person.get("userId") or person["tabNum"]),
                address=str(d.get("address") or ""),
                email_sigma=str(d.get("emailSigma") or ""),
                position=str(d.get("position") or ""),
                phone_mobile=str(d.get("phoneMobile") or ""),
                user_time_zone=d.get("userTimeZone"),
            )
        except Exception as err:  # noqa: BLE001 — без контакта остаётся tabNum
            print(f"[search-contact] профиль без контакта: {err}", file=sys.stderr)
        return info

    def get_info(self) -> Optional[UserInfo]:
        """Профиль с кэшем и дедупликацией (аналог getInfo в user-service.ts).

        Возвращает `None`, если запрос не удался; неудачный результат НЕ
        кэшируется — следующий вызов попробует снова.
        """
        with self._lock:
            if self._info is not None:
                return self._info
            owner = self._in_flight is None
            if owner:
                self._in_flight = threading.Event()
            waiter = self._in_flight

        if not owner:
            waiter.wait()
            return self._info

        info: Optional[UserInfo] = None
        try:
            info = self._perform_fetch()
        finally:
            with self._lock:
                if info is not None:
                    self._info = info
                event, self._in_flight = self._in_flight, None
            event.set()
        return info


def get_user_info(cfg: Optional[ClientConfig] = None) -> Optional[UserInfo]:
    """One-shot профиль без общего кэша — удобно для CLI и проверок."""
    cfg = (cfg or ClientConfig()).resolve()
    return UserInfoService(cfg).get_info()


# ----------------------------------------------------------------------- cli
def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Профиль пользователя (current/person + search-engine/find)")
    ap.add_argument("--print", dest="print_info", action="store_true", help="напечатать описание (по умолчанию)")
    ap.add_argument("--json", action="store_true", help="напечатать поля профиля в JSON")
    ap.add_argument(
        "--auth-mode",
        choices=("ift", "prod", "cert"),
        default=None,
        help="профиль всегда берётся с ИФТ (dev-ветка desktop); режим влияет только на шлюз моделей",
    )
    ap.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    args = ap.parse_args(argv)

    # .env корня репозитория (тот же загрузчик, что у example): адреса, куки.
    try:
        from example.config import load_dotenv

        load_dotenv()
    except Exception:  # noqa: BLE001 — модуль запускается и без example
        pass

    try:
        cfg = (ClientConfig(auth_mode=args.auth_mode) if args.auth_mode else ClientConfig()).resolve()
    except ClientError as err:
        print(f"config error: {err}", file=sys.stderr)
        return 2

    info = UserInfoService(cfg, timeout=args.timeout).get_info()
    if info is None:
        print("не удалось получить профиль (см. stderr)", file=sys.stderr)
        return 1
    if args.json:
        print(json.dumps(info.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(info.description)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "UserInfo",
    "UserInfoService",
    "DEFAULT_TIMEOUT",
    "format_user_info_summary",
    "format_range",
    "get_user_info",
    "short_date",
    "status_text",
]