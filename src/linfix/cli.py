"""CLI LinFix: пользовательские команды и коды возврата (SPEC 6)."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from . import __version__, api, cfdomains, doctor, guard, profiles, state, tgws, util, zapret
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

    print(f"LinFix Linux {__version__}")
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
            print("Обновлено:    никогда (linfix domains update)")
        if info_state.last_error:
            print(f"Посл. ошибка: {info_state.last_error.splitlines()[0]}")
    else:
        print("Zapret:       не найден")
        print("              установите: git clone https://github.com/bol-van/zapret && sudo ./zapret/install_easy.sh")

    tgws_line = status.tgws_state
    if status.tgws_installed and status.state_info.tgws_version:
        tgws_line += f" {status.state_info.tgws_version}"
    print(
        f"TgWsProxy:    {tgws_line}"
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


def cmd_doctor(
    cfg: Config,
    *,
    as_json: bool,
    tls: bool = True,
    tls_timeout: float = 3.0,
) -> int:
    report = api.get_doctor_report(cfg, tls=tls, tls_timeout=tls_timeout)
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
        print("State:    ещё не обновлялся (linfix domains update)")

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
    service = tgws.service_state() if installed else "не установлен"
    listeners = zapret.listening_addresses(cfg.tgws.port)
    state_info = state.load_state()
    version = state_info.tgws_version or "—"
    print(f"Установлен: {'да' if installed else 'нет'} ({cfg.tgws.binary}), версия {version}")
    print(f"Служба:     {service}")
    print(f"Порт:       {', '.join(listeners) if listeners else 'не слушается'}")
    print(f"User unit:  {tgws.user_unit_path()} ({'есть' if tgws.user_unit_path().exists() else 'нет'})")
    config_path = tgws.find_config_path()
    print(f"Конфиг:     {config_path or 'не найден'}")
    return EXIT_OK if installed and service == "active" else EXIT_NOT_INSTALLED


def cmd_tgws_link(cfg: Config, *, qr: bool = False) -> int:
    link = tgws.read_link(cfg.tgws.host, cfg.tgws.port)
    if not link:
        print("не удалось найти конфиг TgWsProxy или секрет", file=sys.stderr)
        return EXIT_NOT_INSTALLED
    print(link)
    if qr:
        try:
            import qrcode  # type: ignore[import-not-found]
        except ImportError:
            print("для --qr установите python3-qrcode (пакет qrcode)", file=sys.stderr)
            return EXIT_NOT_INSTALLED
        code = qrcode.QRCode(border=1)
        code.add_data(link)
        code.make()
        code.print_ascii(invert=True)
    return EXIT_OK


def cmd_tgws_install(cfg: Config, args: argparse.Namespace) -> int:
    try:
        release = tgws.fetch_release(args.version, timeout=args.timeout)
        asset = tgws.select_linux_asset(release, args.arch)
    except tgws.TgwsError as exc:
        print(f"ошибка получения релиза: {exc}", file=sys.stderr)
        return EXIT_ERROR

    state_info = state.load_state()
    already = (
        not args.force
        and state_info.tgws_version == release.tag
        and tgws.is_installed(cfg.tgws.binary)
    )

    if args.dry_run:
        payload = {
            "ok": True,
            "dry_run": True,
            "version": release.tag,
            "asset": asset.name,
            "url": asset.url,
            "sha256": asset.sha256,
            "size": asset.size,
            "dest": cfg.tgws.binary,
            "already_installed": already,
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(f"Версия:  {release.tag}")
            print(f"Asset:   {asset.name} ({asset.size} байт)")
            print(f"SHA256:  {asset.sha256}")
            print(f"Куда:    {cfg.tgws.binary}")
            print(f"Статус:  {'уже установлено' if already else 'будет установлено'}")
        return EXIT_OK

    if not already:
        helper_args = [
            "--url",
            asset.url,
            "--sha256",
            asset.sha256,
            "--version",
            release.tag,
            "--dest",
            cfg.tgws.binary,
            "--state",
            str(state.default_state_path()),
            "--timeout",
            str(args.timeout),
        ]
        try:
            code, payload = util.run_helper(
                "tgws.install", helper_args, timeout=args.timeout + 60
            )
        except util.NetfixError as exc:
            print(f"ошибка: {exc}", file=sys.stderr)
            return EXIT_NO_RIGHTS
        except util.CommandError as exc:
            print(f"ошибка запуска helper: {exc}", file=sys.stderr)
            return EXIT_NO_RIGHTS
        if code != 0:
            message = payload.get("error") or "не удалось установить TgWsProxy"
            print(f"ОШИБКА: {message}", file=sys.stderr)
            return EXIT_NO_RIGHTS if code == 4 else EXIT_ERROR
    else:
        payload = {
            "ok": True,
            "version": release.tag,
            "path": cfg.tgws.binary,
            "sha256": state_info.tgws_sha256,
            "size": None,
            "already_installed": True,
        }

    if not tgws.is_installed(cfg.tgws.binary):
        print(
            "ОШИБКА: helper сообщил об успехе, но бинарник не найден: "
            f"{cfg.tgws.binary}",
            file=sys.stderr,
        )
        return EXIT_CHECK_FAILED

    config_path = None
    config_created = False
    if not args.no_config:
        config_path, config_created = tgws.ensure_config(
            create=True,
            port=cfg.tgws.port,
            host=cfg.tgws.host,
        )

    unit_path = None
    unit_changed = False
    unit_result: dict[str, object] = {}
    mode = tgws.MODE_TRAY
    if not args.no_unit:
        mode_data: dict[str, object] = {}
        requested_mode = getattr(args, "mode", None)
        if config_path is not None:
            mode_data = tgws.load_json_config(config_path)
        if requested_mode:
            mode = str(requested_mode)
            if config_path is not None:
                # Сохраняем выбор в конфиг, иначе автозапуск вернётся к tray.
                mode_data, _backup, _changed = tgws.update_config(
                    config_path, {"linfix_mode": mode}
                )
        elif config_path is not None:
            mode = tgws.effective_mode(config_path)
        if config_path is not None and mode_data.get("linfix_mode") != mode:
            # Фиксируем фактический режим, чтобы автозапуск не вернулся к tray.
            mode_data, _backup, _changed = tgws.update_config(
                config_path, {"linfix_mode": mode}
            )
        if mode == tgws.MODE_CLI:
            api.install_tgws_source(tgws.normalize_version(args.version) or "v1.11.4")
        unit_path, unit_changed = tgws.install_user_unit(
            cfg.tgws.binary, mode=mode, data=mode_data
        )
        unit_result = tgws.enable_user_unit(start=not args.no_start)

    result = {
        **payload,
        "mode": mode,
        "config": str(config_path) if config_path else None,
        "config_created": config_created,
        "unit": str(unit_path) if unit_path else None,
        "unit_changed": unit_changed,
        **unit_result,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(f"TgWsProxy {result.get('version')}: установлен в {result.get('path')}")
        if result.get("already_installed"):
            print("Бинарник уже был актуален, скачивание пропущено")
        if config_path:
            print(f"Конфиг:   {config_path}" + (" (создан)" if config_created else ""))
        if unit_path:
            print(f"User unit: {unit_path}" + (" (обновлён)" if unit_changed else ""))
            if unit_result.get("enabled_ok"):
                print("Автозапуск включён")
            else:
                print(f"Не удалось включить автозапуск: {unit_result.get('enabled_error')}", file=sys.stderr)
            if unit_result.get("started_ok"):
                print("Служба запущена")
            elif unit_result.get("started_ok") is False:
                print(f"Не удалось запустить: {unit_result.get('started_error')}", file=sys.stderr)
        print("Дальше: linfix tgws link")

    if unit_result.get("enabled_ok") is False or unit_result.get("started_ok") is False:
        return EXIT_CHECK_FAILED
    return EXIT_OK


def _bool_choice(value: str) -> bool:
    return value == "on"


def cmd_tgws_config(cfg: Config, args: argparse.Namespace) -> int:
    changes: dict[str, object] = {}
    if args.port is not None:
        changes["port"] = args.port
    if args.host is not None:
        changes["host"] = args.host
    if args.pool_size is not None:
        changes["pool_size"] = args.pool_size
    if args.log_max_mb is not None:
        changes["log_max_mb"] = args.log_max_mb
    if args.verbose is not None:
        changes["verbose"] = _bool_choice(args.verbose)
    if args.h2 is not None:
        changes["h2"] = _bool_choice(args.h2)
    if args.cfproxy is not None:
        changes["cfproxy"] = _bool_choice(args.cfproxy)

    path, created = tgws.ensure_config(
        create=args.create or bool(changes),
        port=cfg.tgws.port,
        host=cfg.tgws.host,
    )
    if path is None:
        print(
            "конфиг TgWsProxy не найден. Запустите TgWsProxy один раз "
            "или используйте --create",
            file=sys.stderr,
        )
        return EXIT_NOT_INSTALLED

    if created and changes:
        data, backup, changed = tgws.update_config(path, changes)
    elif created:
        data = tgws.load_json_config(path)
        backup = None
        changed = False
    elif changes:
        data, backup, changed = tgws.update_config(path, changes)
    else:
        data, backup, changed = tgws.load_json_config(path), None, False

    if args.json:
        safe = {
            key: data.get(key)
            for key in ("host", "port", "cfproxy", "h2", "verbose", "pool_size", "log_max_mb")
            if key in data
        }
        print(
            json.dumps(
                {
                    "ok": True,
                    "path": str(path),
                    "created": created,
                    "changed": changed,
                    "backup": str(backup) if backup else None,
                    "config": safe,
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        print(f"Конфиг: {path}" + (" (создан)" if created else ""))
        if changed:
            print(f"Изменено; бэкап: {backup}")
        elif changes:
            print("Изменений нет")
        for key in ("host", "port", "cfproxy", "h2", "verbose", "pool_size", "log_max_mb"):
            if key in data:
                print(f"  {key} = {data[key]}")
        print("Секрет не показывается; ссылка: linfix tgws link")
    return EXIT_OK


def cmd_tgws_mode(cfg: Config, mode: str) -> int:
    """Переключить режим TgWsProxy (tray/cli) и переустановить юнит."""

    code, payload = api.set_tgws_mode(cfg, mode)
    if code != api.EXIT_OK or payload.get("ok") is False:
        print(f"ошибка: {payload.get('error')}", file=sys.stderr)
        return code if code != api.EXIT_OK else api.EXIT_ERROR
    print(f"режим TgWsProxy: {payload['mode']}")
    print(f"user-юнит: {payload['unit']}")
    print("перезапустите службу: linfix tgws restart")
    return api.EXIT_OK


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
# guard
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# profile (M4)
# ---------------------------------------------------------------------------


def cmd_profile_list(args: argparse.Namespace) -> int:
    loaded = profiles.load_profiles(profiles.PROFILES_PATH)
    current = profiles.current_uuid()
    if not loaded:
        print("профили не заданы, текущий профиль — general")
        return EXIT_OK
    rows = []
    for uuid in sorted(loaded):
        profile = loaded[uuid]
        marker = "✓" if uuid == current else ""
        rows.append([uuid, f"{profile.name}{marker}", profile.strategy])
    _table(["UUID", "Профиль", "Стратегия"], rows)
    if current and current not in loaded:
        print(f"текущее соединение: {current} (профиль не задан, general)")
    return EXIT_OK


def cmd_profile_set(cfg: Config, args: argparse.Namespace) -> int:
    name = args.name or args.uuid
    try:
        code, payload = util.run_helper(
            "profile.set",
            ["--uuid", args.uuid, "--name", name, "--strategy", args.strategy],
        )
    except (util.NetfixError, util.CommandError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return EXIT_NO_RIGHTS
    print(f"profile {payload.get('uuid')}: {'изменено' if payload.get('changed') else 'без изменений'}")
    return EXIT_OK if code == 0 else EXIT_ERROR


def cmd_profile_clear(cfg: Config, args: argparse.Namespace) -> int:
    current = profiles.current_uuid()
    if not current:
        print("активного соединения нет — сначала выберите сеть")
        return EXIT_OK
    try:
        code, payload = util.run_helper("profile.clear", ["--uuid", current])
    except (util.NetfixError, util.CommandError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return EXIT_NO_RIGHTS
    print(
        f"profile {current}: "
        + ("удалён, используется general" if payload.get("changed") else "уже отсутствует")
    )
    return EXIT_OK if code == 0 else EXIT_ERROR


# ---------------------------------------------------------------------------
# guard
# ---------------------------------------------------------------------------


def cmd_guard(cfg: Config, args: argparse.Namespace) -> int:
    if args.state == "check":
        report = guard.check_guard(
            cfg,
            stop_service=(lambda: SimpleNamespace(returncode=0)) if args.no_stop else None,
        )
        if args.json:
            print(json.dumps(report.to_dict(), ensure_ascii=False, indent=2))
        else:
            print(f"{report.status.upper()}: {report.detail}")
            if report.log_path:
                print(f"Лог: {report.log_path}")
        return EXIT_OK if report.status in {"ok", "warn", "disabled"} else EXIT_CHECK_FAILED

    if args.state == "status":
        event = guard.load_event()
        cursor = guard.load_cursor(guard.cursor_path())
        payload = {
            "enabled": cfg.guard.enabled,
            "threshold": cfg.guard.error_threshold,
            "window_seconds": cfg.guard.window_seconds,
            "cursor": asdict(cursor) if cursor else None,
            "last_trip": event.get("last_trip"),
            "last_detail": event.get("detail"),
            "last_status": event.get("status"),
        }
        if args.json:
            print(json.dumps(payload, ensure_ascii=False, indent=2))
        else:
            print(
                f"Guard: {'включён' if cfg.guard.enabled else 'выключен'} "
                f"(порог {cfg.guard.error_threshold}/{cfg.guard.window_seconds} с)"
            )
            if cursor:
                print(f"Offset лога: {cursor.offset}")
            if payload["last_trip"]:
                print(f"Последнее срабатывание: {payload['last_trip']}")
                print(f"  {payload['last_detail']}")
            else:
                print("Срабатываний не было")
        return EXIT_OK

    # on | off
    try:
        code, payload = util.run_helper(
            "config.guard",
            ["--enabled", args.state, "--config", "/etc/linfix/config.toml"],
        )
    except (util.NetfixError, util.CommandError) as exc:
        print(f"ошибка: {exc}", file=sys.stderr)
        return EXIT_NO_RIGHTS

    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    elif payload.get("ok"):
        state_label = "включён" if payload.get("enabled") else "выключен"
        print(
            f"Guard {state_label}: {payload.get('path')}"
            + (f" (бэкап: {payload['backup']})" if payload.get("backup") else "")
        )
    else:
        print(f"ОШИБКА: {payload.get('error', 'не удалось изменить конфиг')}", file=sys.stderr)
    return EXIT_OK if code == 0 else EXIT_NO_RIGHTS


# ---------------------------------------------------------------------------
# update / setup (оркестрация)
# ---------------------------------------------------------------------------


def _domains_update_args(
    *,
    dry_run: bool = False,
    force: bool = False,
    offline: bool = False,
    as_json: bool = False,
    timeout: float = 15.0,
) -> argparse.Namespace:
    return argparse.Namespace(
        dry_run=dry_run,
        force=force,
        offline=offline,
        json=as_json,
        timeout=timeout,
    )


def _tgws_install_args(
    *,
    version: str | None = None,
    dry_run: bool = False,
    force: bool = False,
    no_config: bool = False,
    no_unit: bool = False,
    no_start: bool = False,
    as_json: bool = False,
    timeout: float = 600.0,
    arch: str = "amd64",
) -> argparse.Namespace:
    return argparse.Namespace(
        version=version,
        dry_run=dry_run,
        force=force,
        no_config=no_config,
        no_unit=no_unit,
        no_start=no_start,
        json=as_json,
        timeout=timeout,
        arch=arch,
    )


def _domains_due(cfg: Config, state_info: state.State, *, now: datetime | None = None) -> bool:
    if not state_info.domains_updated_at:
        return True
    try:
        updated = datetime.fromisoformat(state_info.domains_updated_at)
    except ValueError:
        return True
    if now is not None:
        reference = now
    elif updated.tzinfo is not None:
        reference = datetime.now(updated.tzinfo)
    else:
        reference = datetime.now()
    return (reference - updated).days >= cfg.lists.update_days


def cmd_update(cfg: Config, args: argparse.Namespace) -> int:
    state_info = state.load_state()
    domains_due = _domains_due(cfg, state_info)

    latest: str | None = None
    fetch_error: str | None = None
    if not args.domains_only:
        try:
            latest = tgws.fetch_release(timeout=args.timeout).tag
        except tgws.TgwsError as exc:
            fetch_error = str(exc)

    current = state_info.tgws_version
    if current:
        current_label = current
    elif tgws.is_installed(cfg.tgws.binary):
        current_label = "версия неизвестна"
    else:
        current_label = "не установлено"
    print(f"TgWsProxy: {current_label}, доступно {latest or '—'}")
    print(
        "CF-домены: "
        + (
            f"обновлено {state_info.domains_updated_at}"
            if state_info.domains_updated_at
            else "ещё не обновлялись"
        )
        + (" (пора обновить)" if domains_due else " (актуальны)")
    )
    if fetch_error:
        print(f"предупреждение: {fetch_error}", file=sys.stderr)

    actions: list[tuple[str, str, object]] = []
    if domains_due and not args.tgws_only:
        actions.append(
            (
                "CF-домены",
                "linfix domains update",
                lambda: cmd_domains_update(cfg, _domains_update_args(dry_run=args.dry_run)),
            )
        )
    if latest and current != latest and not args.domains_only:
        actions.append(
            (
                "TgWsProxy",
                f"linfix tgws install --version {latest}",
                lambda: cmd_tgws_install(
                    cfg,
                    _tgws_install_args(version=latest, dry_run=args.dry_run),
                ),
            )
        )

    if args.check:
        if not actions:
            print("Обновлений нет")
        else:
            for title, command, _ in actions:
                print(f"Доступно: {title}: {command}")
        return EXIT_OK

    if not actions:
        print("Обновлений нет")
        return EXIT_OK

    if args.dry_run:
        for title, command, _ in actions:
            print(f"[dry-run] {title}: {command}")
        return EXIT_OK

    if not args.yes:
        for _, command, _ in actions:
            print(f"Запустите: {command}")
        return EXIT_OK

    for title, _, action in actions:
        print(f"=== {title} ===")
        code = action()  # type: ignore[operator]
        if code != EXIT_OK:
            return int(code)
    return EXIT_OK


def cmd_setup(cfg: Config, args: argparse.Namespace) -> int:
    info = zapret.detect_zapret(cfg.zapret.service)
    if not info.found:
        print(
            "Zapret не найден. Установите его официальным установщиком:\n"
            "  git clone https://github.com/bol-van/zapret\n"
            "  cd zapret && sudo ./install_easy.sh\n"
            "Выберите nfqws + systemd, затем повторите: linfix setup",
            file=sys.stderr,
        )
        return EXIT_NOT_INSTALLED

    state_info = state.load_state()
    target = zapret.hostlist_target(info, cfg.zapret.hostlist)
    present = zapret.read_hostlist_domains(target) if target else set()
    desired_domains = state_info.domains or cfdomains.FALLBACK_DOMAINS
    missing = [domain for domain in desired_domains if domain not in present]

    steps: list[tuple[str, str, object]] = []
    if missing or not state_info.domains_updated_at:
        steps.append(
            (
                "CF-домены",
                "linfix domains update",
                lambda: cmd_domains_update(cfg, _domains_update_args()),
            )
        )
    if not tgws.is_installed(cfg.tgws.binary):
        steps.append(
            (
                "TgWsProxy",
                "linfix tgws install",
                lambda: cmd_tgws_install(cfg, _tgws_install_args()),
            )
        )
    elif tgws.service_state() != "active":
        steps.append(
            (
                "TgWsProxy (запуск)",
                "linfix tgws start",
                lambda: cmd_tgws_control("start"),
            )
        )

    if not steps:
        print("Всё уже настроено, запускаю doctor")
    else:
        print("План настройки:")
        for title, command, _ in steps:
            print(f"  - {title}: {command}")

    if args.dry_run:
        return EXIT_OK
    if not args.yes:
        print("Для выполнения повторите: linfix setup --yes")
        return EXIT_OK

    for title, _, action in steps:
        print(f"=== {title} ===")
        code = action()  # type: ignore[operator]
        if code != EXIT_OK:
            print(f"Шаг «{title}» завершился с кодом {code}", file=sys.stderr)
            return int(code)

    print("=== doctor ===")
    return cmd_doctor(cfg, as_json=False)


# ---------------------------------------------------------------------------
# parser
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="linfix",
        description="LinFix Linux: zapret + TgWsProxy под контролем",
    )
    parser.add_argument("--config", help="путь к config.toml (по умолчанию /etc/linfix/config.toml)")
    parser.add_argument("--verbose", action="store_true", help="показывать предупреждения конфига")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="краткая сводка о компонентах")

    p_doctor = sub.add_parser("doctor", help="полная диагностика")
    p_doctor.add_argument("--json", action="store_true", help="JSON для GUI")
    p_doctor.add_argument("--no-tls", action="store_true", help="не проверять TLS CF-доменов")
    p_doctor.add_argument("--tls-timeout", type=float, default=3.0, help="таймаут TLS-проверки")

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
    p_install = tgws_sub.add_parser("install", help="скачать, проверить SHA256 и установить")
    p_install.add_argument("--version", help="пиннинг версии (например v1.11.4)")
    p_install.add_argument("--arch", default="amd64", help="архитектура ассета (amd64)")
    p_install.add_argument("--dry-run", action="store_true", help="только показать релиз")
    p_install.add_argument("--force", action="store_true", help="переустановить ту же версию")
    p_install.add_argument("--no-config", action="store_true", help="не создавать конфиг")
    p_install.add_argument("--no-unit", action="store_true", help="не ставить user-unit")
    p_install.add_argument("--no-start", action="store_true", help="не запускать службу")
    p_install.add_argument(
        "--mode", choices=list(tgws.MODES), help="tray (нужен X11) или cli (без графики)"
    )
    p_install.add_argument("--timeout", type=float, default=600.0, help="таймаут скачивания")
    p_install.add_argument("--json", action="store_true", help="JSON для GUI")

    for action in ("start", "stop", "restart", "status"):
        tgws_sub.add_parser(action, help=f"{action} user-службы")
    p_link = tgws_sub.add_parser("link", help="напечатать tg://-ссылку")
    p_link.add_argument("--qr", action="store_true", help="QR в терминале")

    p_config = tgws_sub.add_parser("config", help="безопасно изменить конфиг")
    p_config.add_argument("--port", type=int, help="локальный порт")
    p_config.add_argument("--host", help="адрес прослушивания")
    p_config.add_argument("--pool-size", type=int, help="размер пула")
    p_config.add_argument("--log-max-mb", type=float, help="макс. размер proxy.log, МБ")
    p_config.add_argument("--verbose", choices=["on", "off"])
    p_config.add_argument("--h2", choices=["on", "off"])
    p_config.add_argument("--cfproxy", choices=["on", "off"])
    p_config.add_argument(
        "--mode",
        choices=list(tgws.MODES),
        help="tray (нужен X11) или cli (без графики)",
    )
    p_config.add_argument("--create", action="store_true", help="создать конфиг, если его нет")
    p_config.add_argument("--json", action="store_true", help="JSON для GUI")

    p_logs = sub.add_parser("logs", help="журналы zapret и TgWsProxy")
    p_logs.add_argument("--follow", action="store_true", help="следить за журналом zapret")
    p_logs.add_argument("--lines", type=int, default=50, help="сколько строк показать")

    p_update = sub.add_parser("update", help="обновить TgWsProxy и CF-домены")
    p_update.add_argument("--check", action="store_true", help="только проверить версии")
    p_update.add_argument("--yes", action="store_true", help="выполнить без подтверждения")
    p_update.add_argument("--dry-run", action="store_true", help="показать план")
    p_update.add_argument("--domains-only", action="store_true", help="только CF-домены")
    p_update.add_argument("--tgws-only", action="store_true", help="только TgWsProxy")
    p_update.add_argument("--timeout", type=float, default=20.0, help="таймаут GitHub API")

    p_setup = sub.add_parser("setup", help="мастер первой настройки")
    p_setup.add_argument("--yes", action="store_true", help="выполнить без подтверждения")
    p_setup.add_argument("--dry-run", action="store_true", help="показать план")
    p_setup.add_argument("--no-zapret-install", action="store_true")

    p_guard = sub.add_parser("guard", help="управление guard")
    p_guard.add_argument("state", choices=["check", "status", "on", "off"])
    p_guard.add_argument("--json", action="store_true", help="JSON для GUI")
    p_guard.add_argument("--no-stop", action="store_true", help="не останавливать прокси")

    p_profile = sub.add_parser("profile", help="профили сетей")
    profile_sub = p_profile.add_subparsers(dest="profile_command", required=True)
    profile_sub.add_parser("list", help="список профилей")
    p_profile_set = profile_sub.add_parser("set", help="задать/обновить профиль")
    p_profile_set.add_argument("uuid", help="UUID соединения (или UUID профиля)")
    p_profile_set.add_argument("--name", help="имя профиля (по умолчанию — uuid)")
    p_profile_set.add_argument(
        "--strategy", default=profiles.GENERAL, choices=sorted(profiles.STRATEGIES)
    )
    profile_sub.add_parser("clear", help="сбросить профиль текущего соединения")

    p_uninstall = sub.add_parser("uninstall", help="удалить LinFix (M5)")
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
            return cmd_doctor(
                cfg,
                as_json=args.json,
                tls=not args.no_tls,
                tls_timeout=args.tls_timeout,
            )
        if args.command == "domains":
            if args.domains_command == "list":
                return cmd_domains_list(cfg, offline=args.offline, as_json=args.json)
            if args.domains_command == "test":
                return cmd_domains_test(cfg, args)
            if args.domains_command == "update":
                return cmd_domains_update(cfg, args)
            return _not_implemented("linfix domains update", "M1")
        if args.command == "tgws":
            if args.tgws_command == "status":
                return cmd_tgws_status(cfg)
            if args.tgws_command == "link":
                return cmd_tgws_link(cfg, qr=args.qr)
            if args.tgws_command in {"start", "stop", "restart"}:
                return cmd_tgws_control(args.tgws_command)
            if args.tgws_command == "install":
                return cmd_tgws_install(cfg, args)
            if args.tgws_command == "config":
                if getattr(args, "mode", None):
                    return cmd_tgws_mode(cfg, args.mode)
                return cmd_tgws_config(cfg, args)
        if args.command == "logs":
            return cmd_logs(cfg, args)
        if args.command == "update":
            return cmd_update(cfg, args)
        if args.command == "setup":
            return cmd_setup(cfg, args)
        if args.command == "guard":
            return cmd_guard(cfg, args)
        if args.command == "profile":
            if args.profile_command == "list":
                return cmd_profile_list(args)
            if args.profile_command == "set":
                return cmd_profile_set(cfg, args)
            if args.profile_command == "clear":
                return cmd_profile_clear(cfg, args)
        if args.command == "uninstall":
            return _not_implemented("linfix uninstall", "M5")
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
