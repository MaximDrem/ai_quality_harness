"""CLI для ручной проверки headless-клиента.

Примеры:
    python -m client.cli health --work-dir /tmp/t
    python -m client.cli run --work-dir /tmp/t --message "Привет!"
    python -m client.cli run --work-dir /tmp/t --message "..." --stream
    python -m client.cli run --work-dir /tmp/t --message "О чем презентация?" \
        --attach /path/to/deck.pptx
    python -m client.cli agents --work-dir /tmp/t
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

from .agent.result import AgentResult
from .agent.session import AgentSession
from .core.config import ClientConfig
from .core.errors import AgentError, ClientError
from .http.client import HttpClient


def _parse_args(argv: Optional[list] = None) -> argparse.Namespace:
    p = argparse.ArgumentParser(prog="gigatool-headless client", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--work-dir", required=True, help="рабочая директория инстанса")
    common.add_argument("--model", default=None, help="модель provider/model")
    common.add_argument("--agent", default=None, help="имя агента")
    common.add_argument("--base-dir", default=None, help="OPENCODE_BASE_DIR для инстанса")
    common.add_argument(
        "--skills-dir",
        default=None,
        help="каталог, из которого opencode берёт скиллы (OPENCODE_CONFIG_DIR); "
        "по умолчанию $OPENCODE_BASE_DIR/config, т.е. ~/.wsc/config",
    )
    common.add_argument(
        "--auth-mode",
        choices=("ift", "prod", "cert"),
        default=None,
        help="способ авторизации: ift (cookie-прокси к ИФТ, по умолчанию), "
        "prod (cookie-прокси к пром-стенду) или cert (mTLS-прокси на сертах); "
        "env GIGATOOL_AUTH_MODE",
    )

    h = sub.add_parser("health", parents=[common], help="поднять инстанс и проверить health")
    h.set_defaults(func=_cmd_health)

    r = sub.add_parser("run", parents=[common], help="отправить сообщение и вывести результат")
    r.add_argument("--message", required=True)
    r.add_argument("--stream", action="store_true", help="выводить действия агента по мере поступления")
    r.add_argument("--skip-permissions", action="store_true")
    r.add_argument(
        "--attach",
        action="append",
        default=[],
        metavar="PATH",
        help="приложить файл к сообщению (можно несколько раз); "
        "клиент обрабатывает его так же, как UI desktop",
    )
    r.set_defaults(func=_cmd_run)

    a = sub.add_parser("agents", parents=[common], help="вывести список агентов")
    a.set_defaults(func=_cmd_agents)

    u = sub.add_parser(
        "user-info",
        help="получить профиль пользователя (current/person + search-engine/find)",
    )
    u.add_argument("--auth-mode", choices=("ift", "prod", "cert"), default=None)
    u.add_argument("--json", action="store_true", help="напечатать поля профиля в JSON")
    u.set_defaults(func=_cmd_user_info)

    return p.parse_args(argv)


def _session_from_args(a: argparse.Namespace) -> AgentSession:
    kwargs = {"auth_mode": a.auth_mode} if a.auth_mode else {}
    cfg = ClientConfig(
        model=a.model or "gigachat/GigaChat-3-Ultra",
        agent=a.agent or "build",
        **kwargs,
    ).resolve()
    if a.base_dir:
        cfg.opencode_base_dir = Path(a.base_dir).expanduser()
    if a.skills_dir:
        cfg.skills_dir = Path(a.skills_dir).expanduser()
    return AgentSession(
        work_dir=a.work_dir,
        cfg=cfg,
        model=a.model,
        agent=a.agent or "build",
        skills_dir=cfg.skills_dir,
    )


def _cmd_health(a: argparse.Namespace) -> int:
    with _session_from_args(a) as s:
        ok = s.instance.health() if s.instance else False
        print(f"instances url: {s.url}  health={ok}")
        return 0 if ok else 1


def _print_result(res: AgentResult) -> None:
    print("=== actions ===")
    for tc in res.tool_calls:
        print(f"[tool:{tc.state}] {tc.tool} {json.dumps(tc.input, ensure_ascii=False)[:200]}")
    print("=== reasoning ===")
    for rzn in res.reasonings:
        print(rzn.text)
    print("=== text ===")
    print(res.final_text or "(no text)")
    print("=== artifacts ===")
    for art in res.artifacts:
        print(art.path)


def _stream_actions(
    s: AgentSession, message: str, skip_permissions: bool, attachments: Optional[list] = None
) -> None:
    print("--- streaming agent actions ---")
    for ev in s.actions(message, attachments=attachments, dangerously_skip_permissions=skip_permissions):
        for tc in ev.tool_calls:
            print(f"[tool:{tc.state}] {tc.tool}", flush=True)
        for rzn in ev.reasonings:
            print(f"[reasoning] {rzn.text}", flush=True)
        for t in ev.text_outputs:
            print(f"[text] {t.text}", flush=True)
        if ev.type == "session.status":
            print(f"[status] {ev.properties}", flush=True)


def _cmd_run(a: argparse.Namespace) -> int:
    with _session_from_args(a) as s:
        res: Optional[AgentResult] = None
        attachments = a.attach or None
        if a.stream:
            # Один прогон: действия стримятся, из последнего события сессии
            # собирается итоговый результат (второй ask дублировал бы агента).
            _stream_actions(s, a.message, a.skip_permissions, attachments)
        else:
            res = s.ask(
                a.message,
                attachments=attachments,
                dangerously_skip_permissions=a.skip_permissions,
            )
        if res is None:
            sid = s.session_id or ""
            events = s.client.snapshot_events() if s.client else []
            res = AgentResult(session_id=sid, events=events)
            for ev in events:
                if ev.session_id and ev.session_id != sid:
                    continue
                res.text_outputs += ev.text_outputs
                res.tool_calls += ev.tool_calls
        _print_result(res)
        if res.error:
            print("!!! agent error:", res.error, file=sys.stderr)
        return 1 if res.error else 0


def _cmd_agents(a: argparse.Namespace) -> int:
    with _session_from_args(a) as s:
        http = HttpClient(s.url, s.instance.username, s.instance.password, s.work_dir)
        data = http.get("/app/agents")
        print(json.dumps(data, ensure_ascii=False, indent=2))
    return 0


def _cmd_user_info(a: argparse.Namespace) -> int:
    """Профиль пользователя без запуска агента (проверка current/person)."""
    from .auth.user_info import UserInfoService

    kwargs = {"auth_mode": a.auth_mode} if a.auth_mode else {}
    cfg = ClientConfig(**kwargs).resolve()
    info = UserInfoService(cfg).get_info()
    if info is None:
        print("не удалось получить профиль (см. stderr)", file=sys.stderr)
        return 1
    if a.json:
        print(json.dumps(info.to_dict(), ensure_ascii=False, indent=2))
    else:
        print(info.description)
    return 0


def main(argv: Optional[list] = None) -> int:
    args = _parse_args(argv)
    try:
        return args.func(args) or 0
    except AgentError as e:
        print(f"agent error: {e}", file=sys.stderr)
        return 2
    except ClientError as e:
        print(f"client error: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())