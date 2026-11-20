"""Высокоуровневый API LinFix, общий для CLI и будущей GTK-панели.

Панель не должна дублировать логику: она импортирует этот модуль, а
привилегированные действия вызывает через ``pkexec /usr/libexec/linfix-helper``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from . import cfdomains, doctor, helper, state, tgws, util, zapret
from .config import DEFAULT_CONFIG_PATH, Config, ConfigError, load_config

# Exit codes, идентичные cli (SPEC 6).
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NOT_INSTALLED = 3
EXIT_NO_RIGHTS = 4
EXIT_CHECK_FAILED = 5

#: Источник zapret2 для автоустановки (клон + install_easy.sh).
ZAPRET2_GIT_URL = "https://github.com/bol-van/zapret2"


@dataclass(frozen=True)
class Status:
    zapret: zapret.ZapretInfo
    hostlist: str | None
    cf_present: int
    cf_total: int
    tgws_installed: bool
    tgws_state: str
    listeners: tuple[str, ...]
    config_source: str | None
    state_info: state.State


def get_status(cfg: Config) -> Status:
    info = zapret.detect_zapret(cfg.zapret.service)
    hostlist = zapret.hostlist_target(info, cfg.zapret.hostlist)
    present = zapret.read_hostlist_domains(hostlist) if hostlist else set()
    cf_present = sum(1 for domain in cfdomains.FALLBACK_DOMAINS if domain in present)
    installed = tgws.is_installed(cfg.tgws.binary)
    return Status(
        zapret=info,
        hostlist=hostlist,
        cf_present=cf_present,
        cf_total=len(cfdomains.FALLBACK_DOMAINS),
        tgws_installed=installed,
        tgws_state=tgws.service_state() if installed else "не установлен",
        listeners=tuple(zapret.listening_addresses(cfg.tgws.port)),
        config_source=str(cfg.source) if cfg.source else None,
        state_info=state.load_state(),
    )


def get_doctor_report(
    cfg: Config,
    *,
    tls: bool = True,
    tls_timeout: float = 3.0,
) -> doctor.Report:
    return doctor.run_doctor(cfg, tls=tls, tls_timeout=tls_timeout)


def get_domain_source(
    cfg: Config,
    *,
    offline: bool = False,
    timeout: float = 15.0,
) -> cfdomains.DomainSource:
    return cfdomains.load_domain_source(
        cfg.lists.cf_domains_url, offline=offline, timeout=timeout
    )


def get_domains(
    cfg: Config,
    *,
    offline: bool = False,
    timeout: float = 15.0,
) -> tuple[cfdomains.DomainSource, str | None, set[str]]:
    """Вернуть (источник доменов, целевой hostlist, домены в hostlist)."""

    info = zapret.detect_zapret(cfg.zapret.service)
    hostlist = zapret.hostlist_target(info, cfg.zapret.hostlist)
    present = zapret.read_hostlist_domains(hostlist) if hostlist else set()
    return get_domain_source(cfg, offline=offline, timeout=timeout), hostlist, present


# ---------------------------------------------------------------------------
# GUI helpers: restart, guard, links, tgws config
# ---------------------------------------------------------------------------


def stop_zapret(cfg: Config, *, check: bool = True) -> tuple[int, str]:
    """Остановить zapret (root через helper; без root — честное сообщение)."""

    info = zapret.detect_zapret(cfg.zapret.service)
    if not info.found:
        return EXIT_NOT_INSTALLED, "zapret не найден"

    service = info.service or cfg.zapret.service
    try:
        code, payload = util.run_helper("zapret.stop", ["--service", service])
    except util.NetfixError as exc:
        return EXIT_NO_RIGHTS, str(exc)
    if code != 0:
        return code, str(payload.get("error") or payload.get("state") or "ошибка остановки")

    if check:
        is_active = util.run(["systemctl", "is-active", service], check=False, timeout=10)
        active = (is_active.stdout or "").strip() == "active"
        if active:
            return EXIT_ERROR, f"служба {service} не остановлена"
        # Дайте время на выход процесса и проверьте его наличие.
        time.sleep(0.5)
        if zapret.find_nfqws_processes():
            return EXIT_ERROR, f"процесс nfqws/nfqws2 всё ещё запущен"
    return EXIT_OK, f"{service} остановлена"


def stop_tgws(cfg: Config) -> tuple[int, str]:
    """Остановить TgWsProxy (user-служба)."""

    cp = util.run(["systemctl", "--user", "stop", tgws.UNIT_NAME], check=False, timeout=30)
    if cp.returncode != 0:
        return EXIT_ERROR, f"не удалось остановить tgwsproxy: {(cp.stderr or cp.stdout or '').strip()}"
    state = util.run(["systemctl", "--user", "is-active", tgws.UNIT_NAME], check=False, timeout=10)
    if (state.stdout or "").strip() == "active":
        return EXIT_ERROR, "tgwsproxy не остановлена"
    return EXIT_OK, "tgwsproxy остановлена"


def restart_zapret(cfg: Config, *, check: bool = True) -> tuple[int, str]:
    """Перезапустить zapret. Возвращает (код, детали)."""

    info = zapret.detect_zapret(cfg.zapret.service)
    if not info.found:
        return EXIT_NOT_INSTALLED, "zapret не найден"

    service = info.service or cfg.zapret.service
    completed = util.run(["systemctl", "restart", service], check=False, timeout=40)
    if completed.returncode != 0:
        return EXIT_ERROR, f"systemctl restart {service} завершился с кодом {completed.returncode}"

    if check:
        is_active = util.run(["systemctl", "is-active", service], check=False, timeout=10)
        active = (is_active.stdout or "").strip() == "active"
        if not active:
            return EXIT_CHECK_FAILED, f"служба {service} не active"
        if not zapret.find_nfqws_processes():
            return EXIT_CHECK_FAILED, "процесс nfqws2/nfqws не запущен"
    return EXIT_OK, f"restarted {service}"


def restart_tgws(cfg: Config) -> tuple[int, str]:
    """Перезапустить TgWsProxy (user-служба). Возвращает (код, детали)."""

    restarted = util.run(
        ["systemctl", "--user", "restart", tgws.UNIT_NAME], check=False, timeout=30
    )
    state = util.run(["systemctl", "--user", "is-active", tgws.UNIT_NAME], check=False, timeout=10)
    active = (state.stdout or "").strip() == "active"
    if restarted.returncode != 0 or not active:
        err = (restarted.stderr or restarted.stdout or "").strip()
        return EXIT_ERROR, f"не удалось перезапустить tgwsproxy: {err or f'code={restarted.returncode}'}"
    return EXIT_OK, "tgwsproxy restarted"


def start_tgws(cfg: Config) -> tuple[int, str]:
    """Запустить TgWsProxy (user-служба)."""

    cp = util.run(["systemctl", "--user", "start", tgws.UNIT_NAME], check=False, timeout=30)
    if cp.returncode != 0:
        err = (cp.stderr or cp.stdout or "").strip()
        return EXIT_ERROR, f"не удалось запустить tgwsproxy: {err or f'code={cp.returncode}'}"
    return EXIT_OK, "tgwsproxy запущена"


def start_zapret(cfg: Config) -> tuple[int, str]:
    """Запустить zapret2 (root через helper)."""

    info = zapret.detect_zapret(cfg.zapret.service)
    service = info.service or cfg.zapret.service

    is_active = util.run(["systemctl", "is-active", service], check=False, timeout=10)
    if (is_active.stdout or "").strip() == "active":
        return EXIT_OK, f"{service} уже запущен"

    try:
        code, payload = util.run_helper("zapret.restart", ["--service", service])
    except util.NetfixError as exc:
        return EXIT_NO_RIGHTS, str(exc)
    if code != 0:
        return code, str(payload.get("error") or f"ошибка запуска {service}")
    return EXIT_OK, f"{service} запущен"


def set_tgws_config(changes: dict[str, object]) -> tuple[int, dict[str, object]]:
    """Безопасно изменить конфиг TgWsProxy (бэкап + валидация)."""

    path = tgws.find_config_path() or tgws.config_candidates()[0]
    if not path.exists():
        path, _created = tgws.ensure_config(create=True)
        if path is None:
            return EXIT_NOT_INSTALLED, {"ok": False, "error": "конфиг TgWsProxy не найден"}

    try:
        data, backup, changed = tgws.update_config(path, changes)
    except tgws.TgwsError as exc:
        return EXIT_ERROR, {"ok": False, "error": str(exc)}
    return EXIT_OK, {
        "ok": True,
        "changed": changed,
        "backup": str(backup) if backup else None,
        "path": str(path),
        "config": data,
    }


def toggle_guard(cfg: Config, enabled: bool) -> tuple[int, dict[str, object]]:
    """Включить/выключить guard через helper."""

    code, payload = util.run_helper(
        "config.guard",
        ["--enabled", "on" if enabled else "off", "--config", str(DEFAULT_CONFIG_PATH)],
    )
    return code, payload


def connect_telegram(cfg: Config) -> str | None:
    """Ссылка для Telegram (tg://proxy)."""

    return tgws.read_link(cfg.tgws.host, cfg.tgws.port)


def install_zapret2(cfg: Config) -> tuple[int, dict[str, object]]:
    """Установить zapret2 из репозитория (git clone + install_easy.sh через helper).

    Идемпотентно: если zapret2 уже установлен и запущен — сразу ``ok``.
    """

    info = zapret.detect_zapret("zapret2")
    if info.found and info.service:
        return EXIT_OK, {
            "ok": True,
            "installed": True,
            "service": info.service,
            "install_dir": info.install_dir,
        }

    try:
        code, payload = util.run_helper(
            "zapret.install_easy",
            ["--service", "zapret2", "--git-url", ZAPRET2_GIT_URL],
        )
    except util.NetfixError as exc:
        return EXIT_NO_RIGHTS, {"ok": False, "error": str(exc)}

    if code != 0:
        payload.setdefault("ok", False)
        return code, payload

    after = zapret.detect_zapret("zapret2")
    if not after.found:
        start_zapret(cfg)
        after = zapret.detect_zapret("zapret2")
    payload["installed"] = after.found
    payload["service"] = after.service or "zapret2"
    payload["install_dir"] = after.install_dir
    if not after.found:
        payload["ok"] = False
        payload["error"] = "zapret2 установлен, но служба не запущена"
        return EXIT_CHECK_FAILED, payload
    return EXIT_OK, payload


def install_tgws(
    cfg: Config,
    *,
    version: str | None = None,
    force: bool = False,
    dry_run: bool = False,
) -> tuple[int, dict[str, object]]:
    """Скачать и установить TgWsProxy через helper.

    При отсутствии root/helper возвращает код 4 с честным сообщением.
    """

    release = tgws.fetch_release(version)
    asset = tgws.select_linux_asset(release)
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
    ]
    if force:
        helper_args.append("--force")
    if dry_run:
        helper_args.append("--dry-run")
    try:
        code, payload = util.run_helper("tgws.install", helper_args)
    except util.NetfixError as exc:
        return EXIT_NO_RIGHTS, {"ok": False, "error": str(exc)}

    if code == 0 and not dry_run:
        # Конфиг и user-юнит: без них автозапуск из мастера не сработает.
        config_path, config_created = tgws.ensure_config(
            create=True, port=cfg.tgws.port, host=cfg.tgws.host
        )
        unit_path, unit_changed = tgws.install_user_unit(cfg.tgws.binary)
        payload.update(
            {
                "config": str(config_path) if config_path else None,
                "config_created": config_created,
                "unit": str(unit_path) if unit_path else None,
                "unit_changed": unit_changed,
            }
        )
    return code, payload


def enable_autostart(cfg: Config, *, start: bool = True) -> tuple[int, dict[str, object]]:
    """Включить автозапуск: tgwsproxy (user) + zapret (systemd, root через helper)."""

    enabled: dict[str, bool] = {}
    started: dict[str, bool] = {}

    # TgWsProxy — пользовательская служба: systemctl --user.
    completed = util.run(["systemctl", "--user", "enable", tgws.UNIT_NAME], check=False, timeout=20)
    enabled[tgws.UNIT_NAME] = completed.returncode == 0

    # zapret/zapret2 — системная служба: только root, поэтому через helper.
    info = zapret.detect_zapret(cfg.zapret.service)
    service = info.service or cfg.zapret.service
    try:
        enabled_code, payload = util.run_helper(
            "zapret.set_enabled", ["--service", service, "--enabled", "on"]
        )
        enabled[service] = enabled_code == 0 and bool(payload.get("enabled", payload.get("ok")))
    except util.NetfixError:
        enabled[service] = False

    if start:
        completed = util.run(
            ["systemctl", "--user", "start", tgws.UNIT_NAME], check=False, timeout=30
        )
        started[tgws.UNIT_NAME] = completed.returncode == 0

        started_code, _message = start_zapret(cfg)
        started[service] = started_code == EXIT_OK

    return EXIT_OK, {
        "ok": all(enabled.values()) and all(started.values()),
        "enabled": enabled,
        "started": started,
    }


def set_zapret2_autostart(enabled: bool) -> tuple[int, dict[str, object]]:
    """Включить/выключить автозапуск zapret2 (systemd, root) через helper."""

    code, payload = util.run_helper(
        "zapret.set_enabled",
        ["--service", "zapret2", "--enabled", "on" if enabled else "off"],
    )
    return code, payload


def update_domains(
    cfg: Config, *, offline: bool = False, force: bool = False
) -> tuple[int, dict[str, object]]:
    """Скачать и применить CF-домены (merge + state + рестарт zapret).

    Портирует логику `linfix domains update` на API.
    """

    info = zapret.detect_zapret(cfg.zapret.service)
    target = zapret.hostlist_target(info, cfg.zapret.hostlist)
    if not target:
        return EXIT_NOT_INSTALLED, {"ok": False, "error": "hostlist не определён"}

    state_info = state.load_state()
    source = cfdomains.load_domain_source(cfg.lists.cf_domains_url, offline=offline)
    if source.source == "fallback" and state_info.domains:
        warnings = list(source.warnings)
        warnings.append("используется сохранённый список из state (GitHub недоступен)")
        source = cfdomains.source_from_domains(
            state_info.domains, source="state", warnings=tuple(warnings)
        )

    present = zapret.read_hostlist_domains(target)
    missing = [domain for domain in source.domains if domain not in present]
    digest = state.domains_digest(source.domains)
    if not missing and state_info.domains_sha256 == digest and not force:
        return EXIT_OK, {
            "ok": True,
            "changed": False,
            "reason": "no-changes",
            "domains": len(source.domains),
            "hostlist": target,
        }

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
    except (util.NetfixError, util.CommandError) as exc:
        return EXIT_NO_RIGHTS if isinstance(exc, util.NetfixError) else EXIT_ERROR, {
            "ok": False,
            "error": str(exc),
        }
    return code, {
        **payload,
        "source": source.source,
        "missing_before": missing,
        "hostlist": target,
    }
