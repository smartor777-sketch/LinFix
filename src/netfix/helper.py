"""Привилегированный one-shot helper.

Принимает только фиксированный набор действий и валидированные аргументы.
Никакого ``shell=True``; все пути канонизируются и проверяются на принадлежность
разрешённым директориям (SPEC 5.2 и 15.2).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import util
from .cfdomains import is_valid_domain, merge_hostlist_file, normalize_domain
from .config import DEFAULT_MARKER

ALLOWED_ROOTS: tuple[Path, ...] = tuple(
    Path(path)
    for path in (
        "/opt/netfix",
        "/opt/zapret",
        "/opt/zapret2",
        "/opt/tgwsproxy",
        "/etc/netfix",
        "/var/lib/netfix",
    )
)
ALLOWED_SERVICES = {"zapret", "zapret2"}
ALLOWED_ACTIONS = {"domains.merge", "zapret.restart"}


class HelperError(Exception):
    """Невалидный запрос к helper."""


def _is_under(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def validate_path(raw: str, *, must_exist: bool = False) -> Path:
    path = Path(raw)
    if not path.is_absolute():
        raise HelperError(f"путь должен быть абсолютным: {raw}")
    try:
        resolved = path.resolve(strict=must_exist)
    except FileNotFoundError as exc:
        raise HelperError(f"путь не существует: {raw}") from exc
    if not any(_is_under(resolved, root) for root in ALLOWED_ROOTS):
        raise HelperError(f"путь вне разрешённых директорий: {resolved}")
    if resolved.suffix == ".gz":
        raise HelperError(f"нельзя писать в сжатый список: {resolved}")
    return resolved


def validate_service(raw: str) -> str:
    if raw not in ALLOWED_SERVICES:
        raise HelperError(f"недопустимая служба: {raw}")
    return raw


def validate_action(raw: str) -> str:
    if raw not in ALLOWED_ACTIONS:
        raise HelperError(f"недопустимое действие: {raw}")
    return raw


def validate_domain(raw: str) -> str:
    domain = normalize_domain(raw)
    if not is_valid_domain(domain):
        raise HelperError(f"некорректный домен: {raw}")
    return domain


def action_domains_merge(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    if not args.hostlist:
        raise HelperError("domains.merge: требуется --hostlist")
    if not args.domain:
        raise HelperError("domains.merge: требуется хотя бы один --domain")

    hostlist = validate_path(args.hostlist, must_exist=False)
    domains = [validate_domain(item) for item in args.domain]
    result, backup = merge_hostlist_file(hostlist, domains, marker=args.marker)
    payload: dict[str, object] = {
        "ok": True,
        "hostlist": str(hostlist),
        "added": list(result.added),
        "present": list(result.present),
        "changed": result.changed,
        "backup": str(backup) if backup else None,
    }
    return 0, payload


def action_zapret_restart(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    if not args.service:
        raise HelperError("zapret.restart: требуется --service")
    service = validate_service(args.service)

    restarted = util.run(["systemctl", "restart", service], check=False, timeout=40)
    state = util.run(["systemctl", "is-active", service], check=False, timeout=10)
    active = (state.stdout or "").strip() == "active"

    journal = ""
    if not active or restarted.returncode != 0:
        tail = util.run(
            ["journalctl", "-u", service, "-n", "50", "--no-pager"], check=False, timeout=15
        )
        journal = (tail.stdout or "")[-4000:]

    payload: dict[str, object] = {
        "ok": active,
        "service": service,
        "state": (state.stdout or "").strip() or "unknown",
        "returncode": restarted.returncode,
        "journal": journal,
    }
    return (0 if active else 5), payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="netfix-helper",
        description="Привилегированные действия NetFix (только белый список)",
    )
    parser.add_argument("action", choices=sorted(ALLOWED_ACTIONS))
    parser.add_argument("--hostlist", help="hostlist для domains.merge")
    parser.add_argument("--domain", action="append", default=[], help="домен (можно повторять)")
    parser.add_argument("--marker", default=DEFAULT_MARKER)
    parser.add_argument("--service", help="служба для zapret.restart")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Сначала разбор аргументов: --help и проверка действий должны работать
    # без root. Привилегии проверяются перед выполнением.
    args = build_parser().parse_args(argv)

    if not util.is_root():
        print("netfix-helper: требуются права root (запускайте через pkexec/sudo)", file=sys.stderr)
        return 4

    try:
        validate_action(args.action)
        if args.action == "domains.merge":
            code, payload = action_domains_merge(args)
        else:
            code, payload = action_zapret_restart(args)
    except HelperError as exc:
        payload = {"ok": False, "error": str(exc)}
        print(json.dumps(payload, ensure_ascii=False) if args.json else f"ошибка: {exc}", file=sys.stderr)
        return 2
    except (util.NetfixError, OSError) as exc:
        payload = {"ok": False, "error": str(exc)}
        print(json.dumps(payload, ensure_ascii=False) if args.json else f"ошибка: {exc}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif args.action == "domains.merge":
        print(
            f"{payload['hostlist']}: добавлено {len(payload['added'])}, "
            f"уже было {len(payload['present'])}, бэкап: {payload['backup'] or '—'}"
        )
    else:
        print(f"{payload['service']}: {payload['state']}")
        if payload.get("journal"):
            print(payload["journal"], file=sys.stderr)
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
