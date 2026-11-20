"""Привилегированный one-shot helper.

Принимает только фиксированный набор действий и валидированные аргументы.
Никакого ``shell=True``; все пути канонизируются и проверяются на принадлежность
разрешённым директориям (SPEC 5.2 и 15.2).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
from pathlib import Path

from . import profiles, state, tgws, util, zapret
from .cfdomains import is_valid_domain, merge_hostlist_file, normalize_domain
from .config import DEFAULT_CONFIG_PATH, DEFAULT_MARKER, ConfigError, load_config, set_guard_enabled

ALLOWED_ROOTS: tuple[Path, ...] = tuple(
    Path(path)
    for path in (
        "/opt/linfix",
        "/opt/zapret",
        "/opt/zapret2",
        "/opt/tgwsproxy",
        "/etc/linfix",
        "/var/lib/linfix",
    )
)
ALLOWED_SERVICES = {"zapret", "zapret2"}
ALLOWED_ACTIONS = {
    "config.guard",
    "domains.apply",
    "domains.merge",
    "profile.apply",
    "profile.clear",
    "profile.set",
    "tgws.install",
    "zapret.restart",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


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


def validate_sha256(raw: str) -> str:
    value = (raw or "").strip().lower()
    if not value:
        return ""
    if not SHA256_RE.match(value):
        raise HelperError("некорректный --source-sha256 (ожидается 64 hex-символа)")
    return value


def validate_source_url(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        return ""
    if not value.lower().startswith("https://"):
        raise HelperError("--source-url должен быть HTTPS")
    return value


def restart_and_verify(
    service: str,
    *,
    attempts: int = 8,
    delay: float = 0.25,
) -> tuple[bool, str, str]:
    """Перезапустить zapret и убедиться, что служба и процесс nfqws живы."""

    restarted = util.run(["systemctl", "restart", service], check=False, timeout=40)
    wanted = "nfqws2" if service.endswith("2") else "nfqws"
    alive = False
    for _ in range(attempts):
        if any(Path(args[0]).name == wanted for _, args in zapret.find_nfqws_processes()):
            alive = True
            break
        time.sleep(delay)

    status_cp = util.run(["systemctl", "is-active", service], check=False, timeout=10)
    active = (status_cp.stdout or "").strip() == "active"
    ok = restarted.returncode == 0 and active and alive

    journal = ""
    if not ok:
        tail = util.run(
            ["journalctl", "-u", service, "-n", "50", "--no-pager"], check=False, timeout=15
        )
        journal = (tail.stdout or "")[-4000:]
    detail = f"active={active}, процесс {wanted}={'жив' if alive else 'не найден'}"
    return ok, detail, journal


def apply_domains(
    hostlist: str | Path,
    domains: list[str] | tuple[str, ...],
    *,
    marker: str,
    service: str,
    restart_on_change: bool,
    state_path: str | Path,
    source_label: str | None,
    source_url: str | None,
    source_sha256: str = "",
    restart_func=None,
) -> tuple[int, dict[str, object]]:
    """Merge + state + (рестарт и проверка) + откат при провале."""

    expected_digest = state.domains_digest(domains)
    if source_sha256 and expected_digest != source_sha256:
        raise HelperError("sha256 списка не совпадает с ожидаемым")

    result, backup = merge_hostlist_file(hostlist, domains, marker=marker)
    current = state.load_state(state_path).record_domains(
        domains,
        source_label=source_label,
        source_url=source_url,
        hostlist=hostlist,
        service=service,
    )

    restarted = False
    rolled_back = False
    detail = ""
    journal = ""
    runner = restart_func or restart_and_verify

    if result.changed and restart_on_change:
        restarted = True
        ok, detail, journal = runner(service)
        if not ok:
            if backup is not None:
                util.restore_file(backup, hostlist)
                rolled_back = True
                ok2, detail2, journal2 = runner(service)
                detail = f"{detail}; откат выполнен, повторный рестарт: {detail2}"
                journal = journal2 or journal
            current = current.record_error(detail + ("\n" + journal if journal else ""))
            current.save(state_path)
            return 5, {
                "ok": False,
                "hostlist": str(hostlist),
                "service": service,
                "added": list(result.added),
                "present": list(result.present),
                "changed": result.changed,
                "backup": str(backup) if backup else None,
                "restarted": restarted,
                "rollback": rolled_back,
                "state": str(state_path),
                "detail": detail,
                "journal": journal,
            }

    current.save(state_path)
    return 0, {
        "ok": True,
        "hostlist": str(hostlist),
        "service": service,
        "added": list(result.added),
        "present": list(result.present),
        "changed": result.changed,
        "backup": str(backup) if backup else None,
        "restarted": restarted,
        "rollback": rolled_back,
        "state": str(state_path),
        "detail": detail,
        "journal": journal,
    }


def action_domains_apply(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    if not args.hostlist:
        raise HelperError("domains.apply: требуется --hostlist")
    if not args.domain:
        raise HelperError("domains.apply: требуется хотя бы один --domain")
    if not args.service:
        raise HelperError("domains.apply: требуется --service")

    hostlist = validate_path(args.hostlist, must_exist=False)
    state_path = validate_path(args.state, must_exist=False)
    service = validate_service(args.service)
    domains = [validate_domain(item) for item in args.domain]
    source_url = validate_source_url(args.source_url)
    source_sha256 = validate_sha256(args.source_sha256)

    return apply_domains(
        hostlist,
        domains,
        marker=args.marker,
        service=service,
        restart_on_change=args.restart_on_change,
        state_path=state_path,
        source_label=args.source_label,
        source_url=source_url or None,
        source_sha256=source_sha256,
    )


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


def action_config_guard(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    if args.enabled not in {"on", "off"}:
        raise HelperError("config.guard: --enabled должен быть on|off")
    target = validate_path(args.config or str(DEFAULT_CONFIG_PATH), must_exist=False)
    existed = target.exists()
    text = ""
    if existed:
        with target.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            text = handle.read()

    want = args.enabled == "on"
    new_text = set_guard_enabled(text, want)
    changed = new_text != text
    backup = None
    if changed:
        if existed:
            backup = util.backup_file(target)
        util.atomic_write(target, new_text, mode=0o644)
        try:
            updated = load_config(target)
        except ConfigError as exc:
            if backup is not None:
                util.restore_file(backup, target)
            elif target.exists():
                target.unlink()
            raise HelperError(f"после правки конфиг некорректен: {exc}") from exc
        if updated.guard.enabled != want:
            if backup is not None:
                util.restore_file(backup, target)
            raise HelperError("не удалось изменить [guard] enabled")

    return 0, {
        "ok": True,
        "changed": changed,
        "path": str(target),
        "backup": str(backup) if backup else None,
        "enabled": want,
    }


def _profile_uuid(value: str) -> str:
    try:
        return profiles.validate_uuid(value)
    except ValueError as exc:
        raise HelperError(str(exc)) from exc


def _profile_name(value: str) -> str:
    try:
        return profiles.validate_name(value)
    except ValueError as exc:
        raise HelperError(str(exc)) from exc


def _profile_strategy(value: str) -> str:
    try:
        return profiles.validate_strategy(value)
    except ValueError as exc:
        raise HelperError(str(exc)) from exc


def action_profile_set(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    if not args.uuid:
        raise HelperError("profile.set: требуется --uuid")
    if not args.name:
        raise HelperError("profile.set: требуется --name")

    uuid = _profile_uuid(args.uuid)
    name = _profile_name(args.name)
    strategy = _profile_strategy(args.strategy or profiles.GENERAL)

    result = profiles.set_profile(uuid, name, strategy)
    return 0, {
        "ok": True,
        "uuid": uuid,
        "name": name,
        "strategy": strategy,
        "changed": result["changed"],
        "profiles": {key: {"name": value.name, "strategy": value.strategy} for key, value in result["profiles"].items()},
    }


def action_profile_clear(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    if not args.uuid:
        raise HelperError("profile.clear: требуется --uuid")

    uuid = _profile_uuid(args.uuid)
    result = profiles.clear_profile(uuid)
    return 0, {
        "ok": True,
        "uuid": uuid,
        "changed": result["changed"],
        "profiles": {key: {"name": value.name, "strategy": value.strategy} for key, value in result["profiles"].items()},
    }


def action_profile_apply(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    if not args.uuid:
        raise HelperError("profile.apply: требуется --uuid")

    uuid = _profile_uuid(args.uuid)
    loaded = profiles.load_profiles(profiles.PROFILES_PATH)
    profile = profiles.resolve_profile(loaded, uuid)
    return 0, {
        "ok": True,
        "uuid": uuid,
        "name": profile.name,
        "strategy": profile.strategy,
    }


def action_tgws_install(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    if not args.url:
        raise HelperError("tgws.install: требуется --url")
    if not args.sha256:
        raise HelperError("tgws.install: требуется --sha256")

    url = validate_source_url(args.url)
    sha256 = validate_sha256(args.sha256)
    dest = validate_path(args.dest or str(tgws.BINARY), must_exist=False)
    state_path = validate_path(args.state, must_exist=False)
    version = tgws.normalize_version(args.version) or "unknown"

    info = tgws.install_from_url(url, dest, sha256, timeout=args.timeout)
    current = state.load_state(state_path).record_tgws(version, str(info["sha256"]), dest)
    current.save(state_path)

    return 0, {
        "ok": True,
        "version": version,
        "path": str(dest),
        "sha256": info["sha256"],
        "size": info["size"],
        "state": str(state_path),
    }


def action_zapret_restart(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    if not args.service:
        raise HelperError("zapret.restart: требуется --service")
    service = validate_service(args.service)

    restarted = util.run(["systemctl", "restart", service], check=False, timeout=40)
    status_cp = util.run(["systemctl", "is-active", service], check=False, timeout=10)
    active = (status_cp.stdout or "").strip() == "active"

    journal = ""
    if not active or restarted.returncode != 0:
        tail = util.run(
            ["journalctl", "-u", service, "-n", "50", "--no-pager"], check=False, timeout=15
        )
        journal = (tail.stdout or "")[-4000:]

    payload: dict[str, object] = {
        "ok": active,
        "service": service,
        "state": (status_cp.stdout or "").strip() or "unknown",
        "returncode": restarted.returncode,
        "journal": journal,
    }
    return (0 if active else 5), payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="linfix-helper",
        description="Привилегированные действия LinFix (только белый список)",
    )
    parser.add_argument("action", choices=sorted(ALLOWED_ACTIONS))
    parser.add_argument("--hostlist", help="hostlist для domains.merge")
    parser.add_argument("--domain", action="append", default=[], help="домен (можно повторять)")
    parser.add_argument("--marker", default=DEFAULT_MARKER)
    parser.add_argument("--service", help="служба zapret")
    parser.add_argument("--state", default=str(state.default_state_path()), help="путь к state.json")
    parser.add_argument("--source-label", default=None, help="github | state | fallback")
    parser.add_argument("--source-url", default="")
    parser.add_argument("--source-sha256", default="")
    parser.add_argument(
        "--restart-on-change",
        action="store_true",
        help="перезапустить zapret, если hostlist изменился",
    )
    parser.add_argument("--url", default="", help="URL бинарника для tgws.install")
    parser.add_argument("--sha256", default="", help="ожидаемый SHA256")
    parser.add_argument("--version", default="", help="версия TgWsProxy")
    parser.add_argument("--dest", default=str(tgws.BINARY), help="куда установить бинарник")
    parser.add_argument("--timeout", type=float, default=600.0, help="таймаут скачивания")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="путь к config.toml")
    parser.add_argument("--enabled", choices=["on", "off"], help="для config.guard")
    parser.add_argument("--uuid", help="для profile.*")
    parser.add_argument("--name", help="для profile.set")
    parser.add_argument("--strategy", default=profiles.GENERAL, help="для profile.set")
    parser.add_argument("--json", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    # Сначала разбор аргументов: --help и проверка действий должны работать
    # без root. Привилегии проверяются перед выполнением.
    args = build_parser().parse_args(argv)

    if not util.is_root():
        print("linfix-helper: требуются права root (запускайте через pkexec/sudo)", file=sys.stderr)
        return 4

    try:
        validate_action(args.action)
        if args.action == "domains.merge":
            code, payload = action_domains_merge(args)
        elif args.action == "domains.apply":
            code, payload = action_domains_apply(args)
        elif args.action == "tgws.install":
            code, payload = action_tgws_install(args)
        elif args.action == "config.guard":
            code, payload = action_config_guard(args)
        elif args.action == "profile.set":
            code, payload = action_profile_set(args)
        elif args.action == "profile.clear":
            code, payload = action_profile_clear(args)
        elif args.action == "profile.apply":
            code, payload = action_profile_apply(args)
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
    elif args.action in {"domains.merge", "domains.apply"}:
        print(
            f"{payload['hostlist']}: добавлено {len(payload['added'])}, "
            f"уже было {len(payload['present'])}, бэкап: {payload['backup'] or '—'}"
        )
        if payload.get("restarted"):
            print(f"рестарт {payload['service']}: {'да' if payload['ok'] else 'ошибка'}")
        if payload.get("journal"):
            print(payload["journal"], file=sys.stderr)
    elif args.action == "tgws.install":
        print(
            f"TgWsProxy {payload['version']}: {payload['path']} "
            f"({payload['size']} байт, sha256 {str(payload['sha256'])[:12]}…)"
        )
    elif args.action == "config.guard":
        state_label = "включён" if payload["enabled"] else "выключен"
        print(
            f"guard {state_label}: {payload['path']}"
            + (f" (бэкап: {payload['backup']})" if payload.get("backup") else "")
        )
    elif args.action in {"profile.set", "profile.clear", "profile.apply"}:
        if args.action == "profile.apply":
            print(f"profile {payload['uuid']}: {payload['name']} ({payload['strategy']})")
        else:
            changed = "изменено" if payload.get("changed") else "без изменений"
            print(f"profile {payload['uuid']}: {changed}")
    else:
        print(f"{payload['service']}: {payload['state']}")
        if payload.get("journal"):
            print(payload["journal"], file=sys.stderr)
    return code


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
