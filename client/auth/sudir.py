"""Автологин в СУДИР (SberFriend gateway) по логину+паролю и работа с куками.

Назначение: headless-клиент (cookie-прокси / sidecar) для обращения к
SberFriend gateway использует Sudir-сессионные куки (GIGACHAT_SESSION_COOKIES).
В desktop-приложении их собирает BrowserWindow (sudir-auth.ts); здесь тот же
OIDC-поток (Keycloak realm на idp) повторяется обычными HTTP-запросами — без
графического окна.

Поток (стандартный Keycloak login-actions):
  1. GET  <SHARED_API_URL>/              -> 302 на IdP /auth/realms/*/openid-connect/auth
  2. GET  auth URL                       -> HTML-форма «СУДИР» (username/password)
  3. POST /login-actions/authenticate    username,password -> 302 к redirect_uri c `code`
  4. GET  redirect_uri?code=...          -> gateway выдаёт финальные куки

Итог — Cookie-строка для GIGACHAT_SESSION_COOKIES.

Безопасность: локально читает креды из .env (IFT_LOGIN_USER/IFT_PASSWORD /
IFT_LOGING) или env, отправляет их на корпоративный IdP только для получения
своей сессии. Пароли в stdout/log не печатаются.
"""
from __future__ import annotations

import argparse
import http.cookiejar
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.parse
import urllib.request

SHARED_API_URL = os.environ.get(
    "SHARED_API_URL",
    "https://shared-api.ift.sberfriend.delta.sbrf.ru",
)

UA = "GigaChat-GigaCat"

# Домены, на которых живёт gateway-сессия. Куки IdP (KC_*, AUTH_SESSION_ID)
# попадают в jar тоже, но приоритет — у сессионных кук gateway.
SESSION_COOKIE_DOMAINS = ("sbrf.ru", "sberfriend.delta.sbrf.ru")

_FORM_ATTR_RE = re.compile(r'([\w-]+)="([^"]*)"')
_FORM_RE = re.compile(r"<form\b([^>]*)>", re.I)
_INPUT_RE = re.compile(r"<input\b([^>]*)>", re.I)
_FORM_WINDOW = 8000
_MAX_BODY = 400_000


def _build_opener(insecure: bool = True) -> urllib.request.OpenerDirector:
    cj = http.cookiejar.CookieJar()
    handlers = [urllib.request.HTTPCookieProcessor(cj)]
    if insecure:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    opener = urllib.request.build_opener(*handlers)
    opener.addheaders = [("User-Agent", UA), ("Accept", "text/html,application/json,*/*")]
    return opener


def _cookie_jar(opener: urllib.request.OpenerDirector) -> http.cookiejar.CookieJar:
    for h in opener.handlers:
        if isinstance(h, urllib.request.HTTPCookieProcessor):
            return h.cookiejar
    return http.cookiejar.CookieJar()


def _parse_login_form(html: str):
    """Вернуть (action_url, {field_name: value}) из формы «СУДИР»."""
    m = _FORM_RE.search(html)
    if not m:
        raise RuntimeError("не найдена login-форма СУДИР")
    attrs = dict(_FORM_ATTR_RE.findall(m.group(1)))
    action = attrs.get("action")
    if not action:
        raise RuntimeError("в форме нет action")
    fields = {}
    block = html[m.end(): m.end() + _FORM_WINDOW]
    for inp in _INPUT_RE.finditer(block):
        a = dict(_FORM_ATTR_RE.findall(inp.group(1)))
        nm = a.get("name")
        if nm:
            fields[nm] = a.get("value", "")
    return action, fields


def fetch_login_form(base_url: str = None):
    """GET корня и страницы IdP, вернуть (form_action, fields, resp_url).

    Только чтение — НЕ отправляет креды.
    """
    base = (base_url or SHARED_API_URL).rstrip("/")
    opener = _build_opener()
    try:
        resp = opener.open(base + "/", timeout=30)
    except urllib.error.HTTPError as e:
        resp = e
    html = resp.read(_MAX_BODY).decode("utf-8", "replace")
    action, fields = _parse_login_form(html)
    return action, fields, str(resp.url)


def header_to_dict(header: str) -> dict:
    """Cookie-header строка -> dict."""
    out: dict = {}
    for part in (header or "").split(";"):
        k, _, v = part.strip().partition("=")
        if k:
            out[k.strip()] = v
    return out


def _cookies_from_jar(jar: http.cookiejar.CookieJar) -> str:
    session_cookies = []
    other_cookies = []
    for c in jar:
        entry = f"{c.name}={c.value}"
        if any(suffix in (c.domain or "") for suffix in SESSION_COOKIE_DOMAINS):
            session_cookies.append(entry)
        else:
            other_cookies.append(entry)
    # Gateway-сессия почти всегда на .sbrf.ru; если её не видим — отдаём все куки
    # (чтобы скрипт не терял PLATFORM_SESSION при нестандартном домене).
    final_cookies = session_cookies or other_cookies or [f"{c.name}={c.value}" for c in jar]
    return "; ".join(final_cookies)


def login(username: str, password: str, base_url: str = None) -> str:
    """Выполнить Sudir-логин и вернуть Cookie-заголовок (name=value; ...)."""
    base = (base_url or SHARED_API_URL).rstrip("/")
    opener = _build_opener()
    cj = _cookie_jar(opener)

    # 1. GET корня -> 302 на IdP обрабатывает HTTPRedirectHandler автоматически.
    try:
        resp = opener.open(base + "/", timeout=30)
    except urllib.error.HTTPError as e:
        resp = e
    html = resp.read(_MAX_BODY).decode("utf-8", "replace")

    # 2. Разобрать форму СУДИР (action + поля)
    action, fields = _parse_login_form(html)

    # 3. POST пароля на action (абсолютный относительно resp.url)
    post_vals = dict(fields)
    post_vals["username"] = username
    post_vals["formattedUsername"] = username
    post_vals["password"] = password
    post_vals.setdefault("login", "Продолжить")
    action_url = urllib.parse.urljoin(str(resp.url), action)
    data = urllib.parse.urlencode(post_vals).encode("utf-8")
    try:
        resp2 = opener.open(
            urllib.request.Request(action_url, data=data, method="POST",
                                   headers={"Content-Type": "application/x-www-form-urlencoded"}),
            timeout=60,
        )
        resp2.read(300_000)
    except urllib.error.HTTPError:
        # Redirect handler следует 302; не-2xx здесь не важен для сбора кук.
        pass

    # 4. Собрать куки. Redirect handler прошёл цепочку (form -> code ->
    #    redirect_uri), поэтому в jar живут и IdP-куки, и сессионные куки gateway.
    return _cookies_from_jar(cj)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Не идти по редиректу: статус ответа нужен как есть.

    Без этого 302 на IdP превращается в 200 (страница логина), и
    session-check «подтверждает» заведомо битые куки.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def verify_session(cookie_header: str, base_url: str = None) -> int:
    """GET /session-check с куками, без следования редиректам.

    Возвращает HTTP-статус первого ответа (200 = валидна, 302 = уводит
    на логин, то есть куки не приняты).
    """
    base = (base_url or SHARED_API_URL).rstrip("/")
    # Cookie-заголовок передан явно: cookie-процессор только мешает
    # (добавляет куки из jar и маскирует явный заголовок).
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    opener = urllib.request.build_opener(_NoRedirect(), urllib.request.HTTPSHandler(context=ctx))
    req = urllib.request.Request(
        base + "/session-check",
        headers={"Cookie": cookie_header, "User-Agent": UA, "Accept": "application/json"},
    )
    try:
        resp = opener.open(req, timeout=30)
        return resp.status
    except urllib.error.HTTPError as e:
        return e.code
    except Exception:
        return 0


def write_cookies_env(path: str, cookie_header: str) -> None:
    """Перезаписать/добавить GIGACHAT_SESSION_COOKIES=<header> в .env-файл."""
    p = os.path.abspath(path)
    lines = []
    if os.path.exists(p):
        with open(p, "r", encoding="utf-8") as f:
            lines = f.read().splitlines()
    out = []
    written = False
    for ln in lines:
        if ln.strip().startswith("GIGACHAT_SESSION_COOKIES="):
            if not written:
                out.append(f'GIGACHAT_SESSION_COOKIES="{cookie_header}"')
                written = True
            continue
        out.append(ln)
    if not written:
        out.append(f'GIGACHAT_SESSION_COOKIES="{cookie_header}"')
    with open(p, "w", encoding="utf-8") as f:
        f.write("\n".join(out) + "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Sudir login -> Cookie для GIGACHAT_SESSION_COOKIES")
    ap.add_argument("--username", help="логин (default env IFT_LOGIN_USER / IFT_LOGING)")
    ap.add_argument("--password", help="пароль (default env IFT_PASSWORD)")
    ap.add_argument("--base", default=None, help="SHARED_API_URL (default из env)")
    ap.add_argument("--format", choices=["header", "json"], default="header")
    ap.add_argument("--check-form", action="store_true",
                    help="только GET и показать форму (поля) — НЕ отправлять креды")
    ap.add_argument("--verify", action="store_true",
                    help="после логина проверить куки через GET /session-check (ожидается 200)")
    ap.add_argument("--write-env", metavar="PATH",
                    help="записать GIGACHAT_SESSION_COOKIES в файл (.env)")
    args = ap.parse_args(argv)

    if args.check_form:
        try:
            action, fields, url = fetch_login_form(args.base)
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1
        print(f"page_url={url}")
        print(f"action={action}")
        for k, v in fields.items():
            print(f"field {k}={v!r}" if len(v) < 60 else f"field {k}=<len {len(v)}>")
        return 0

    username = args.username or os.environ.get("IFT_LOGIN_USER") or os.environ.get("IFT_LOGING")
    password = args.password or os.environ.get("IFT_PASSWORD")
    if not username or not password:
        print("ERROR: нужны username/password (args или env IFT_LOGIN_USER / IFT_PASSWORD)", file=sys.stderr)
        return 2
    try:
        header = login(username, password, args.base)
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 1
    if not header:
        print("ERROR: логин не вернул куки. Проверь креды/сеть.", file=sys.stderr)
        return 1
    st = 200
    if args.verify:
        st = verify_session(header, args.base)
        print(f"session-check -> {st} ({'OK' if st == 200 else 'FAIL'})")
        if st != 200:
            print("WARN: куки не валидны", file=sys.stderr)
    if args.write_env:
        write_cookies_env(args.write_env, header)
        print(f"GIGACHAT_SESSION_COOKIES записаны в {os.path.abspath(args.write_env)}")
        return 0 if st == 200 else 1
    if args.format == "json":
        print(json.dumps(header_to_dict(header), ensure_ascii=False))
    else:
        print(header)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())