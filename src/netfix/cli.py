"""CLI NetFix: пользовательские команды и коды возврата (SPEC 6)."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path

from . import __version__, api, cfdomains, doctor, profiles, state, tgws, util, zapret
from .config import Config, ConfigError, load_config

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NOT_INSTALLED = 3
EXIT_NO_RIGHTS = 4
EXIT_CHECK_FAILED = 5

STATUS_LABELS = {doctor.PASS: "PASS", doctor.WARN: "WARN", doctor.FAIL: "FAIL"}


def _table(headers: list[str], rows: list[list[object]]) -> None:
    widths = [len(header) for header in headers]
    for row in rows:
        for index, cell in enumerate(row):
            widths[index] = max(widths[index], len(str(cell)))

    def render(row: list[object]) -> str:
        return "  ".join(str(cell).ljust(widths[index]) for index, cell in enumerate(row))

    print(render(list(headers)))
    print("  ".join("-" * width for width in widths))
    for row in rows:
        print(render(row))


def _print_warnings(cfg: Config, *, verbose: bool) -> None:
    if not verbose:
        return
    for warning in cfg.warnings:
        print(f"предупреждение: {warning}", file=sys.stderr)


def _not_implemented(what: str, milestone: str) -> int:
    print(f"{what}: будет реализовано в {milestone}", file=sys.stderr)
    return EXIT_ERROR


# ---------------------------------------------------------------------------
# status
# ---------------------------------------------------------------------------


def cmd_status(cfg: Config, *, verbose: bool = False) -> int:
    status = api.get_status(cfg)
    info = status.zapret

    print(f"NetFix Linux {__version__}")
    print(f"Система:      {util.os_pretty_name()}, Python {platform.python_version()}")

    if info.found:
        state = "active" if info.active else "inactive"
        print(f"Zapret:       {info.service} — {state} ({info.enabled_state}), pid {info.pid or '—'}")
        binary_name = Path(info.binary).name if info.binary else "—"
        print(f"Установка:    {info.install_dir or '—'} ({binary_name})")
        print(
            f"Очередь:      qnum {info.qnum if info.qnum is not None else '—'}, "
            f"fwmark {info.fwmark or '—'}, user {info.user or '—'}"
        )
        print(f"Hostlist:     {status.hostlist or 'не определён'}")
        if status.hostlist:
            print(f"CF-домены:    {status.cf_present}/{status.cf_total} в hostlist")
        else:
            print("CF-домены:    hostlist не определён")
        info_state = status.state_info
        if info_state.domains_updated_at:
            short_sha = (info_state.domains_sha256 or "")[:12]
            print(
                f"Обновлено:    {info_state.domains_updated_at} "
                f"({info_state.domains_count} доменов, sha {short_sha or '—'})"
            )
        else:
            print("Обновлено:    никогда (netfix domains update)")
        if info_state.last_error:
            print(f"Посл. ошибка: {info_state.last_error.splitlines()[0]}")
    else:
        print("Zapret:       не найден")
        print("              установите: git clone https://github.com/bol-van/zapret && sudo ./zapret/install_easy.sh")

    print(
        f"TgWsProxy:    {status.tgws_state}"
        + ("" if status.tgws_installed else f" ({cfg.tgws.binary})")
    )

    port_state = f"LISTEN {', '.join(status.listeners)}" if status.listeners else "свободен"
    print(f"Порт {cfg.tgws.port}:     {port_state}")

    guard_state = "включён (M3)" if cfg.guard.enabled else "выключен"
    print(f"Guard:        {guard_state}")
    print(f"Конфиг:       {cfg.source or 'значения по умолчанию'}")

    if verbose:
        for warning in info.warnings:
            print(f"предупреждение: {warning}", file=sys.stderr)
    return EXIT_OK


# ---------------------------------------------------------------------------
# doctor
# ---------------------------------------------------------------------------


def cmd_doctor(cfg: Config, *, as_json: bool) -> int:
    report = api.get_doctor_report(cfg)
    if as_json:
        print(report.to_json())
    else:
        rows = [
            [check.title, STATUS_LABELS.get(check.status, check.status), check.details, check.hint]
            for check in report.checks
        ]
        _table(["Проверка", "Статус", "Детали", "Подсказка"], rows)
    return EXIT_OK if report.ok else EXIT_CHECK_FAILED


# ---------------------------------------------------------------------------
# domains
# ---------------------------------------------------------------------------


def _domain_source(cfg: Config, *, offline: bool) -> cfdomains.DomainSource:
    return api.get_domain_source(cfg, offline=offline)


def _select_source(
    cfg: Config,
    state_info: state.State,
    *,
    offline: bool,
    timeout: float = 15.0,
) -> cfdomains.DomainSource:
    """Выбрать источник: GitHub, state (если он свежее fallback) или fallback."""

    source = api.get_domain_source(cfg, offline=offline, timeout=timeout)
    warnings = list(source.warnings)

    if offline and state_info.domains:
        warnings.append("используется сохранённый список из state")
        return cfdomains.source_from_domains(state_info.domains, source="state", warnings=tuple(warnings))

    if not offline and source.source == "fallback" and state_info.domains:
        warnings.append("GitHub недоступен, используется сохранённый список из state")
        return cfdomains.source_from_domains(state_info.domains, source="state", warnings=tuple(warnings))

    return source


def cmd_domains_list(cfg: Config, *, offline: bool, as_json: bool = False) -> int:
    info = zapret.detect_zapret(cfg.zapret.service)
    target = zapret.hostlist_target(info, cfg.zapret.hostlist)
    state_info = state.load_state()
    source = _select_source(cfg, state_info, offline=offline)
    present = zapret.read_hostlist_domains(target) if target else set()
    missing = [domain for domain in source.domains if domain not in present]

    if as_json:
        payload = {
            "source": source.source,
            "source_total": source.total,
            "source_invalid": source.invalid_count,
            "domains": list(source.domains),
            "hostlist": target,
            "present": [domain for domain in source.domains if domain in present],
            "missing": missing,
            "state": state_info.to_dict(),
            "warnings": list(source.warnings),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return EXIT_OK if target else EXIT_NOT_INSTALLED

    print(
        f"Источник: {source.source}, доменов: {len(source.domains)}, "
        f"строк: {source.total}, невалидных: {source.invalid_count}"
    )
    for warning in source.warnings:
        print(f"предупреждение: {warning}", file=sys.stderr)
    print(f"Hostlist: {target or 'не определён'}")
    if state_info.domains_updated_at:
        print(
            f"State:    обновлено {state_info.domains_updated_at}, "
            f"доменов {state_info.domains_count}, sha {(state_info.domains_sha256 or '')[:12]}"
        )
    else:
        print("State:    ещё не обновлялся (netfix domains update)")

    rows = [
        ["OK" if domain in present else "MISSING", domain] for domain in source.domains
    ]
    _table(["Статус", "Домен"], rows)

    if not target:
        print("zapret/hostlist не найден — добавлять некуда", file=sys.stderr)
        return EXIT_NOT_INSTALLED
    return EXIT_OK


def cmd_domains_update(cfg: Config, args: argparse.Namespace) -> int:
    info = zapret.detect_zapret(cfg.zapret.service)
    target = zapret.hostlist_target(info, cfg.zapret.hostlist)
    if not target:
        print(
            "zapret/hostlist не найден. Установите zapret и повторите.",
            file=sys.stderr,
        )
        return EXIT_NOT_INSTALLED

    state_info = state.load_state()
    source = _select_source(
        cfg,
        state_info,
        offline=args.offline,
        timeout=args.timeout,
    )
    for warning in source.warnings:
        print(f"предупреждение: {warning}", file=sys.stderr)
    if not source.domains:
        print("получен пустой список доменов — обновление отменено", file=sys.stderr)
        return EXIT_CHECK_FAILED

    present = zapret.read_hostlist_domains(target)
    missing = [domain for domain in source.domains if domain not in present]
    digest = state.domains_digest(source.domains)
    unchanged = (
        not missing
        and state_info.domains_sha256 == digest
        and state_info.hostlist == target
        and not state_info.last_error
    )

    if unchanged and not args.force:
        if args.json:
            print(
                json.dumps(
                    {
                        "ok": True,
                        "changed": False,
                        "reason": "no-changes",
                        "source": source.source,
                        "hostlist": target,
                        "domains": len(source.domains),
                        "digest": digest,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
        else:
            print(
                f"Изменений нет: {len(source.domains)} доменов уже в hostlist, "
                "state актуален"
            )
        return EXIT_OK

    restart_needed = bool(missing) and cfg.zapret.restart_on_change

    if args.dry_run:
        payload = {
            "ok": True,
            "dry_run": True,
            "source": source.source,
            "hostlist": target,
            "domains": len(source.domains),
            "present": len(source.domains) - len(missing),
            "missing": missing,
            "digest": digest,
            "restart": restart_needed,
            "service": info.service,
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"Источник: {source.source}, доменов: {len(source.domains)}")
            print(f"Hostlist: {target}")
            print(f"Добавить: {len(missing)}, уже есть: {len(source.domains) - len(missing)}")
            print(f"Digest:   {digest}")
            print(f"Рестарт:  {'да' if restart_needed else 'нет'}")
        return EXIT_OK

    helper_args = [
        "--hostlist",
        target,
        "--marker",
        cfg.lists.marker,
        "--service",
        info.service or cfg.zapret.service,
        "--state",
        str(state.default_state_path()),
        "--source-label",
        source.source,
        "--source-url",
        cfg.lists.cf_domains_url,
        "--source-sha256",
        digest,
    ]
    for domain in source.domains:
        helper_args.extend(["--domain", domain])
    if cfg.zapret.restart_on_change:
        helper_args.append("--restart-on-change")

    try:
        code, payload = util.run_helper("domains.apply", helper_args)
    except util.NetfixError as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return EXIT_NO_RIGHTS
    except util.CommandError as exc:
        print(f"ошибка запуска helper: {exc}", file=sys.stderr)
        return EXIT_NO_RIGHTS

    if args.json:
        print(
            json.dumps(
                {
                    **payload,
                    "source": source.source,
                    "missing_before": missing,
                    "digest": digest,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    elif payload.get("ok"):
        print(f"Источник: {source.source}, доменов: {len(source.domains)}")
        print(f"Hostlist: {payload.get('hostlist')}")
        print(
            f"Добавлено: {len(payload.get('added', []))}, "
            f"уже было: {len(payload.get('present', []))}"
        )
        if payload.get("backup"):
            print(f"Бэкап:    {payload['backup']}")
        print(
            f"Рестарт:  {'да' if payload.get('restarted') else 'нет'}"
            + (f" ({payload['detail']})" if payload.get("detail") else "")
        )
        if payload.get("rollback"):
            print("ВНИМАНИЕ: hostlist откатан к бэкапу", file=sys.stderr)
    else:
        message = payload.get("error") or payload.get("detail") or "не удалось применить домены"
        print(f"ОШИБКА: {message}", file=sys.stderr)
        if payload.get("journal"):
            print(payload["journal"], file=sys.stderr)

    if code == 0:
        return EXIT_OK
    if code in (EXIT_USAGE, EXIT_NO_RIGHTS, EXIT_CHECK_FAILED):
        return code

    error_text = str(payload.get("error", "")).lower()
    if any(marker in error_text for marker in ("sudo", "root", "privileg", "no new privileges")):
        return EXIT_NO_RIGHTS
    return EXIT_ERROR


def cmd_domains_test(cfg: Config, args: argparse.Namespace) -> int:
    source = _domain_source(cfg, offline=args.offline)
    limit = None if args.all else 3
    results = cfdomains.test_domains(
        source.domains,
        limit=limit,
        timeout=args.timeout,
    )
    ok = all(result_ok for _, result_ok, _ in results)

    if args.json:
        payload = {
            "ok": ok,
            "source": source.source,
            "results": [
                {"host": host, "status": "pass" if result_ok else "fail", "details": details}
                for host, result_ok, details in results
            ],
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        print(f"Источник: {source.source}; проверено хостов: {len(results)}")
        rows = [["OK" if result_ok else "FAIL", host, details] for host, result_ok, details in results]
        _table(["Статус", "Хост", "Детали"], rows)
    return EXIT_OK if ok else EXIT_CHECK_FAILED


# ---------------------------------------------------------------------------
# tgws
# ---------------------------------------------------------------------------


def cmd_tgws_status(cfg: Config) -> int:
    installed = tgws.is_installed(cfg.tgws.binary)
    state = tgws.service_state() if installed else "не установлен"
    listeners = zapret.listening_addresses(cfg.tgws.port)
    print(f"Установлен: {'да' if installed else 'нет'} ({cfg.tgws.binary})")
    print(f"Служба:     {state}")
    print(f"Порт:       {', '.join(listeners) if listeners else 'не слушается'}")
    config_path = tgws.find_config_path()
    print(f"Конфиг:     {config_path or 'не найден'}")
    return EXIT_OK if installed and state == "active" else EXIT_NOT_INSTALLED


def cmd_tgws_link(cfg: Config) -> int:
    link = tgws.read_link(cfg.tgws.host, cfg.tgws.port)
    if not link:
        print("не удалось найти конфиг TgWsProxy или секрет", file=sys.stderr)
        return EXIT_NOT_INSTALLED
    print(link)
    return EXIT_OK


def cmd_tgws_control(action: str) -> int:
    completed = util.run(["systemctl", "--user", action, tgws.UNIT_NAME], check=False, timeout=40)
    if completed.stdout.strip():
        print(completed.stdout.strip())
    if completed.returncode != 0:
        if completed.stderr.strip():
            print(completed.stderr.strip(), file=sys.stderr)
        return EXIT_ERROR
    return EXIT_OK


# ---------------------------------------------------------------------------
# logs
# ---------------------------------------------------------------------------


def _tail(path: Path, lines: int) -> str:
    try:
        content = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return f"не удалось прочитать {path}: {exc}"
    return "\n".join(content.splitlines()[-lines:])


def cmd_logs(cfg: Config, args: argparse.Namespace) -> int:
    info = zapret.detect_zapret(cfg.zapret.service)
    unit = info.service or cfg.zapret.service

    if args.follow:
        if util.which("journalctl") is None:
            print("journalctl не найден", file=sys.stderr)
            return EXIT_ERROR
        os.execvp("journalctl", ["journalctl", "-u", unit, "-f"])

    sections: list[tuple[str, list[str]]] = [
        (
            f"zapret ({unit})",
            ["journalctl", "-u", unit, "-n", str(args.lines), "--no-pager"],
        ),
        (
            f"tgwsproxy (user), последние {args.lines} строк",
            ["journalctl", "--user", "-u", tgws.UNIT_NAME, "-n", str(args.lines), "--no-pager"],
        ),
    ]
    for title, command in sections:
        completed = util.run(command, check=False, timeout=20)
        print(f"=== {title} ===")
        text = (completed.stdout or completed.stderr or "").strip()
        print(text or "(нет данных)")
        print()

    log_path = tgws.find_log_path()
    if log_path:
        print(f"=== {log_path} ===")
        print(_tail(log_path, args.lines))
    else:
        print("=== proxy.log ===")
        print("(файл не найден; используется journald)")
    return EXIT_OK


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="netfix",
        description="NetFix Linux: zapret + TgWsProxy под контролем",
    )
    parser.add_argument("--config", help="путь к config.toml (по умолчанию /etc/netfix/config.toml)")
    parser.add_argument("--verbose", action="store_true", help="показывать предупреждения конфига")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="краткая сводка о компонентах")

    p_doctor = sub.add_parser("doctor", help="полная диагностика")
    p_doctor.add_argument("--json", action="store_true", help="JSON для GUI")

    p_domains = sub.add_parser("domains", help="CF-домены TgWsProxy")
    domains_sub = p_domains.add_subparsers(dest="domains_command", required=True)
    p_list = domains_sub.add_parser("list", help="показать домены и их наличие в hostlist")
    p_list.add_argument("--offline", action="store_true", help="не обращаться к GitHub")
    p_list.add_argument("--json", action="store_true", help="JSON для GUI")

    p_update = domains_sub.add_parser("update", help="скачать и применить домены")
    p_update.add_argument("--force", action="store_true", help="применить, даже если изменений нет")
    p_update.add_argument("--dry-run", action="store_true", help="только показать план, без правок")
    p_update.add_argument("--offline", action="store_true", help="использовать state/fallback")
    p_update.add_argument("--json", action="store_true", help="JSON для GUI")
    p_update.add_argument("--timeout", type=float, default=15.0, help="таймаут загрузки, секунды")
    p_test = domains_sub.add_parser("test", help="TLS-тест доменов")
    p_test.add_argument("--all", action="store_true", help="проверить все домены (по умолчанию 3)")
    p_test.add_argument("--json", action="store_true", help="JSON для GUI")
    p_test.add_argument("--timeout", type=float, default=5.0, help="таймаут TLS, секунды")
    p_test.add_argument("--offline", action="store_true", help="использовать fallback-список")

    p_tgws = sub.add_parser("tgws", help="TgWsProxy")
    tgws_sub = p_tgws.add_subparsers(dest="tgws_command", required=True)
    p_install = tgws_sub.add_parser("install", help="скачать и установить (M2)")
    p_install.add_argument("--version", help="пиннинг версии")
    for action in ("start", "stop", "restart", "status"):
        tgws_sub.add_parser(action, help=f"{action} user-службы")
    p_link = tgws_sub.add_parser("link", help="напечатать tg://-ссылку")
    p_link.add_argument("--qr", action="store_true", help="QR в терминале (M2)")
    tgws_sub.add_parser("config", help="безопасно изменить конфиг (M2)")

    p_logs = sub.add_parser("logs", help="журналы zapret и TgWsProxy")
    p_logs.add_argument("--follow", action="store_true", help="следить за журналом zapret")
    p_logs.add_argument("--lines", type=int, default=50, help="сколько строк показать")

    p_update = sub.add_parser("update", help="обновить TgWsProxy и домены (M1/M2)")
    p_update.add_argument("--check", action="store_true", help="только проверить версии")

    p_setup = sub.add_parser("setup", help="мастер первой настройки (M3)")
    p_setup.add_argument("--yes", action="store_true")
    p_setup.add_argument("--no-zapret-install", action="store_true")

    p_guard = sub.add_parser("guard", help="управление guard (M3)")
    p_guard.add_argument("state", choices=["on", "off"])

    p_profile = sub.add_parser("profile", help="профили сетей (M4)")
    profile_sub = p_profile.add_subparsers(dest="profile_command", required=True)
    profile_sub.add_parser("list", help="список профилей")
    p_set = profile_sub.add_parser("set", help="назначить профиль")
    p_set.add_argument("uuid")
    p_set.add_argument("name")
    profile_sub.add_parser("clear", help="сбросить профиль")

    p_uninstall = sub.add_parser("uninstall", help="удалить NetFix (M5)")
    p_uninstall.add_argument("--keep-config", action="store_true")

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        cfg = load_config(args.config)
    except ConfigError as exc:
        print(f"ошибка конфига: {exc}", file=sys.stderr)
        return EXIT_USAGE

    util.setup_logging(cfg.general.log_level)
    _print_warnings(cfg, verbose=args.verbose)

    try:
        if args.command == "status":
            return cmd_status(cfg, verbose=args.verbose)
        if args.command == "doctor":
            return cmd_doctor(cfg, as_json=args.json)
        if args.command == "domains":
            if args.domains_command == "list":
                return cmd_domains_list(cfg, offline=args.offline, as_json=args.json)
            if args.domains_command == "test":
                return cmd_domains_test(cfg, args)
            if args.domains_command == "update":
                return cmd_domains_update(cfg, args)
            return _not_implemented("netfix domains update", "M1")
        if args.command == "tgws":
            if args.tgws_command == "status":
                return cmd_tgws_status(cfg)
            if args.tgws_command == "link":
                if args.qr:
                    return _not_implemented("netfix tgws link --qr", "M2")
                return cmd_tgws_link(cfg)
            if args.tgws_command in {"start", "stop", "restart"}:
                return cmd_tgws_control(args.tgws_command)
            if args.tgws_command == "install":
                return _not_implemented("netfix tgws install", "M2")
            return _not_implemented("netfix tgws config", "M2")
        if args.command == "logs":
            return cmd_logs(cfg, args)
        if args.command == "update":
            return _not_implemented("netfix update", "M1/M2")
        if args.command == "setup":
            return _not_implemented("netfix setup", "M3")
        if args.command == "guard":
            return _not_implemented("netfix guard", "M3")
        if args.command == "profile":
            if args.profile_command == "list":
                loaded = profiles.load_profiles("/etc/netfix/profiles.toml")
                if not loaded:
                    print("профили не настроены (используется general)")
                for uuid, profile in loaded.items():
                    print(f"{uuid}: {profile.name} (strategy={profile.strategy})")
                return EXIT_OK
            return _not_implemented("netfix profile set/clear", "M4")
        if args.command == "uninstall":
            return _not_implemented("netfix uninstall", "M5")
    except util.CommandError as exc:
        print(f"ошибка команды: {exc}", file=sys.stderr)
        return EXIT_ERROR
    except util.NetfixError as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return EXIT_ERROR

    parser.print_help()
    return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
