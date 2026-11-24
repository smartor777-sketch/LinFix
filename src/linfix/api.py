"""Высокоуровневый API LinFix, общий для CLI и будущей GTK-панели.

Панель не должна дублировать логику: она импортирует этот модуль, а
привилегированные действия вызывает через ``pkexec /usr/libexec/linfix-helper``.
"""

from __future__ import annotations

import shutil
import tarfile
import tempfile
import time
from pathlib import Path
from dataclasses import dataclass

from . import (
    blockcheck,
    cfdomains,
    doctor,
    guard,
    helper,
    state,
    tgws,
    updates,
    util,
    zapret,
)
from .config import DEFAULT_CONFIG_PATH, Config, ConfigError, load_config

# Exit codes, идентичные cli (SPEC 6).
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2
EXIT_NOT_INSTALLED = 3
EXIT_NO_RIGHTS = 4
EXIT_CHECK_FAILED = 5
EXIT_BLOCKED = 6

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


def clear_ipv6_blackholes() -> tuple[bool, str]:
    """Убрать маршруты-заглушки IPv6 через helper (нужен root).

    Возвращает (успех, сообщение для пользователя).
    """

    try:
        code, payload = util.run_helper("net.clear_ipv6_blackholes", [])
    except util.NetfixError as exc:
        return False, str(exc)

    outstanding = payload.get("outstanding") or []
    if code == 0 and not outstanding:
        removed = int(payload.get("removed") or 0)
        if removed:
            return True, f"Заглушки убраны: удалено маршрутов {removed}"
        return True, "Заглушек не было — всё чисто"

    if code == 3:
        return False, str(payload.get("error") or "VPN ещё работает — убирать заглушки нельзя")
    if outstanding:
        return False, f"Остались маршруты ({len(outstanding)}) — нужен root"
    return False, str(payload.get("error") or "не удалось убрать заглушки")


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

    if blockcheck.running():
        return (
            EXIT_BLOCKED,
            "идёт подбор стратегии (blockcheck2): zapret включится сам, когда замеры закончатся",
        )

    info = zapret.detect_zapret(cfg.zapret.service)
    service = info.service or cfg.zapret.service

    is_active = util.run(["systemctl", "is-active", service], check=False, timeout=10)
    if (is_active.stdout or "").strip() == "active":
        return EXIT_OK, f"{service} уже запущен"

    # Осиротевшие оболочки blockcheck2 (helper убили по таймауту) тоже
    # вмешиваются в трафик: пока они живы, стартовать zapret нельзя.
    if blockcheck.stray_processes():
        return (
            EXIT_BLOCKED,
            "завершаются процессы blockcheck2.sh — дождитесь их окончания, "
            "затем включите zapret вручную",
        )

    try:
        code, payload = util.run_helper("zapret.restart", ["--service", service])
    except util.NetfixError as exc:
        return EXIT_NO_RIGHTS, str(exc)
    if code != 0:
        return code, str(payload.get("error") or f"ошибка запуска {service}")
    return EXIT_OK, f"{service} запущен"


#: Источник доменов для ротации TgWsProxy.
ROTATION_USER = "user"
ROTATION_GITHUB = "github"
ROTATION_MODES = (ROTATION_USER, ROTATION_GITHUB)


def _rotation_list(data: dict[str, object], key: str) -> list[str]:
    value = data.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def get_tgws_rotation() -> tuple[int, dict[str, object]]:
    """Откуда TgWsProxy берёт домены для ротации прямо сейчас."""

    path = tgws.find_config_path()
    data: dict[str, object] = {}
    if path is not None:
        try:
            data = tgws.load_json_config(path)
        except tgws.TgwsError:
            data = {}
    user_domains = _rotation_list(data, "cfproxy_user_domain")
    worker_domains = _rotation_list(data, "cfproxy_worker_domain")
    github_domains = list(state.load_state().domains)
    if user_domains:
        mode = ROTATION_USER
        domains = user_domains
    else:
        # Пустой cfproxy_user_domain: TgWsProxy при старте сам тянет
        # список с GitHub (если user-доменов нет, автообновление включено).
        mode = ROTATION_GITHUB
        domains = github_domains
    return EXIT_OK, {
        "ok": True,
        "mode": mode,
        "domains": domains,
        "worker_domains": worker_domains,
        "auto": mode == ROTATION_GITHUB,
        "github_count": len(github_domains),
        "saved": tgws.load_rotation_domains(),
        "config": str(path) if path is not None else None,
    }


def set_tgws_rotation(
    cfg: Config,
    mode: str,
    domains: list[str] | None = None,
) -> tuple[int, dict[str, object]]:
    """Переключить ротация TgWsProxy: пользовательские домены или GitHub.

    Перед изменением конфиг и user-unit бэкапируются, после — служба
    перезапускается. Возврат к GitHub не удаляет пользовательский список:
    он сохраняется и восстанавливается при обратном переключении.
    """

    if mode not in ROTATION_MODES:
        return EXIT_USAGE, {
            "ok": False,
            "error": f"неизвестный источник доменов: {mode}",
        }
    path = tgws.find_config_path()
    if path is None:
        path, _created = tgws.ensure_config(
            create=True, port=cfg.tgws.port, host=cfg.tgws.host
        )
        if path is None:
            return EXIT_NOT_INSTALLED, {"ok": False, "error": "конфиг TgWsProxy не найден"}
    try:
        data = tgws.load_json_config(path)
    except tgws.TgwsError as exc:
        return EXIT_ERROR, {"ok": False, "error": str(exc)}
    current = _rotation_list(data, "cfproxy_user_domain")

    if mode == ROTATION_USER:
        target = list(domains) if domains is not None else (tgws.load_rotation_domains() or current)
        if not target:
            return EXIT_USAGE, {
                "ok": False,
                "error": "нет сохранённых пользовательских доменов для ротации",
            }
        try:
            tgws.save_rotation_domains(target)
        except tgws.TgwsError as exc:
            return EXIT_ERROR, {"ok": False, "error": str(exc)}
        changes: dict[str, object] = {"cfproxy_user_domain": target}
        result_domains = target
    else:
        if current:
            # Сохраняем, чтобы вернуть этот же список при обратном переключении.
            try:
                tgws.save_rotation_domains(current)
            except tgws.TgwsError:
                pass
        changes = {"cfproxy_user_domain": []}
        result_domains = list(state.load_state().domains)

    code, payload = set_tgws_config(changes)
    if code != EXIT_OK or payload.get("ok") is False:
        return code, payload
    try:
        updated = tgws.load_json_config(path)
        unit, unit_changed = tgws.install_user_unit(
            cfg.tgws.binary, mode=tgws.effective_mode(path), data=updated
        )
    except tgws.TgwsError as exc:
        return EXIT_ERROR, {"ok": False, "error": str(exc)}
    util.run(["systemctl", "--user", "daemon-reload"], check=False, timeout=20)
    restarted = util.run(
        ["systemctl", "--user", "restart", tgws.UNIT_NAME], check=False, timeout=60
    )
    return EXIT_OK, {
        **payload,
        "mode": mode,
        "domains": result_domains,
        "unit": str(unit),
        "unit_changed": unit_changed,
        "restarted": restarted.returncode == 0,
        "restart_error": (restarted.stderr or "").strip(),
    }


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
    """Включить/выключить guard: флаг в конфиге + user-таймер проверок."""

    code, payload = util.run_helper(
        "config.guard",
        ["--enabled", "on" if enabled else "off", "--config", str(DEFAULT_CONFIG_PATH)],
    )
    timer = guard.ensure_timer(enabled)
    payload.update(timer)
    if code == 0 and not timer["timer_ok"]:
        # Конфиг записан, но таймер не переключился — это не успех.
        payload["ok"] = False
        payload.setdefault("error", "не удалось переключить таймер guard")
        return EXIT_ERROR, payload
    return code, payload


def connect_telegram(cfg: Config) -> str | None:
    """Ссылка для Telegram (tg://proxy)."""

    return tgws.read_link(cfg.tgws.host, cfg.tgws.port)


def check_updates(*, timeout: float = 20.0) -> updates.Found:
    """Проверить обновления zapret2 и TgWsProxy (без установки)."""

    return updates.check(timeout=timeout)


def update_zapret2(cfg: Config) -> tuple[int, dict[str, object]]:
    """Обновить zapret2, сохранив наш конфиг и hostlists.

    Порядок: сохранить профиль → git pull/install_easy.sh → вернуть профиль.
    Если установка или восстановление не удались, возвращаем ошибку с
    понятным текстом: конфиг к этому моменту уже восстановлен из бэкапа.
    """

    # Бэкап пишется в /var/lib/linfix, то есть нужен root — идём через
    # helper, как и для самой установки. Прямой вызов падал с
    # PermissionError, и пользователь видел «отказано в доступе».
    try:
        code, meta_payload = util.run_helper("zapret.save_profile", [], timeout=120)
    except util.NetfixError as exc:
        return EXIT_NO_RIGHTS, {"ok": False, "error": str(exc)}
    if code != 0:
        return EXIT_NO_RIGHTS, {
            "ok": False,
            "error": str(meta_payload.get("error") or "не удалось сохранить профили"),
        }
    meta = meta_payload

    # Обновляем из релизного архива: в git-архиве zapret2 бинарников нет.
    # Конфиг не трогаем — профили CF/rutracker уже сохранены в бэкап.
    try:
        remote = updates.fetch_remote()
    except updates.UpdateError as exc:
        return EXIT_ERROR, {"ok": False, "error": str(exc), "backup": meta}

    helper_args = [
        "--service", "zapret2",
        "--update",
        "--zapret-version", remote.zapret_tag or "",
        "--asset", remote.zapret_asset or "",
    ]
    if remote.zapret_asset_url:
        helper_args += ["--asset-url", remote.zapret_asset_url]
    if remote.zapret_asset_sha256:
        helper_args += ["--asset-sha256", remote.zapret_asset_sha256]

    try:
        code, payload = util.run_helper(
            "zapret.install_easy", helper_args, timeout=900
        )
    except util.NetfixError as exc:
        return EXIT_NO_RIGHTS, {"ok": False, "error": str(exc), "backup": meta}
    except util.CommandError as exc:
        return EXIT_ERROR, {
            "ok": False,
            "error": f"обновление zapret2 не завершилось за 15 минут: {exc}",
            "backup": meta,
        }

    payload["backup"] = meta
    if code != 0:
        payload.setdefault("ok", False)
        return code, payload

    # install_easy.sh пишет в config — возвращаем наши профили и домены.
    try:
        restored = util.run_helper("zapret.restore_profile", [])
        payload["restore"] = restored[1] if restored[0] == 0 else {"ok": False}
    except util.NetfixError as exc:
        payload["restore"] = {"ok": False, "error": str(exc)}

    if not payload["restore"].get("ok"):
        payload["ok"] = False
        payload["error"] = (
            "zapret2 обновлён, но профиль не восстановлен: "
            + str(payload["restore"].get("error") or "неизвестная ошибка")
            + ". Бэкап: /var/lib/linfix/zapret-profile"
        )
        return EXIT_CHECK_FAILED, payload

    restart_code = restart_zapret(cfg)
    payload["ok"] = restart_code == EXIT_OK
    payload["installed"] = True
    return EXIT_OK if payload["ok"] else EXIT_CHECK_FAILED, payload


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
            timeout=1800,
        )
    except util.NetfixError as exc:
        return EXIT_NO_RIGHTS, {"ok": False, "error": str(exc)}
    except util.CommandError as exc:
        return EXIT_ERROR, {
            "ok": False,
            "error": f"установка zapret2 не завершилась за 30 минут: {exc}",
        }

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
    Бинарник ~57 МБ: таймаут helper'а — 10 минут, иначе на медленном канале
    установка обрывается на 120 секундах.
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
        "--timeout",
        "600",
    ]
    if force:
        helper_args.append("--force")
    if dry_run:
        helper_args.append("--dry-run")
    try:
        code, payload = util.run_helper("tgws.install", helper_args, timeout=660)
    except util.NetfixError as exc:
        return EXIT_NO_RIGHTS, {"ok": False, "error": str(exc)}
    except util.CommandError as exc:
        return EXIT_ERROR, {
            "ok": False,
            "error": f"установка TgWsProxy не завершилась за 11 минут: {exc}",
        }

    if code == 0 and not dry_run:
        code, payload = _finalize_tgws_install(cfg, payload, version=release.tag)
    return code, payload


def _finalize_tgws_install(
    cfg: Config, payload: dict[str, object], *, version: str
) -> tuple[int, dict[str, object]]:
    """Конфиг, режим работы и user-юнит после установки TgWsProxy."""

    config_path, config_created = tgws.ensure_config(
        create=True, port=cfg.tgws.port, host=cfg.tgws.host
    )
    mode = tgws.effective_mode(config_path)
    data = tgws.load_json_config(config_path) if config_path is not None else {}
    # Режим живёт в файле LinFix: TgWsProxy затирает незнакомые ключи
    # в своём config.json при каждом старте.
    tgws.save_mode(mode)

    if mode == tgws.MODE_CLI:
        code, source_payload = install_tgws_source(version)
        payload["source"] = source_payload
        if code == 0:
            payload["deps"] = ensure_source_deps()
        if code != 0:
            payload["ok"] = False
            payload["error"] = str(
                source_payload.get("error")
                or "не удалось скачать исходники TgWsProxy для CLI-режима"
            )
            return EXIT_ERROR, payload

    unit_path, unit_changed = tgws.install_user_unit(
        cfg.tgws.binary, mode=mode, data=data
    )
    # Перезапускаем всегда: после обновления в памяти остался бы старый
    # бинарник, и версия в state.json разошлась бы с работающей.
    unit_result = tgws.enable_user_unit(start=True, restart=True)
    payload.update(
        {
            "mode": mode,
            "config": str(config_path) if config_path else None,
            "config_created": config_created,
            "unit": str(unit_path) if unit_path else None,
            "unit_changed": unit_changed,
            **unit_result,
        }
    )
    return EXIT_OK, payload


def install_tgws_source(version: str) -> tuple[int, dict[str, object]]:
    """Скачать и распаковать исходники TgWsProxy для CLI-режима.

    Без root: каталог в домашней директории пользователя, поэтому режим
    переключается без запроса пароля.
    """

    target = tgws.source_dir()
    if (target / "proxy" / "tg_ws_proxy.py").exists():
        return EXIT_OK, {"ok": True, "path": str(target), "cached": True}

    url = f"https://github.com/{tgws.REPO}/archive/refs/tags/{version}.tar.gz"
    try:
        archive = tgws.download_to_temp(url, Path("/tmp"), timeout=60, attempts=3)
    except tgws.TgwsError as exc:
        return EXIT_ERROR, {"ok": False, "error": str(exc)}

    staging = Path(tempfile.mkdtemp(prefix="linfix-tgws-src."))
    try:
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(staging, filter="data")
        roots = [p for p in staging.iterdir() if p.is_dir()]
        package = roots[0] / "proxy" / "tg_ws_proxy.py" if len(roots) == 1 else None
        if package is None or not package.exists():
            return EXIT_ERROR, {"ok": False, "error": "в архиве нет пакета proxy"}
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(roots[0], target)
        (target / "VERSION").write_text(version + "\n", encoding="utf-8")
    except (OSError, tarfile.TarError) as exc:
        return EXIT_ERROR, {"ok": False, "error": f"распаковка исходников: {exc}"}
    finally:
        shutil.rmtree(staging, ignore_errors=True)
        archive.unlink(missing_ok=True)

    return EXIT_OK, {"ok": True, "path": str(target), "version": version}


def source_deps_missing() -> list[str]:
    """Модули, нужные CLI-режиму и отсутствующие в системе."""

    import importlib.util

    return [name for name in ("httpx", "h2") if importlib.util.find_spec(name) is None]


def ensure_source_deps() -> dict[str, object]:
    """Поставить python-зависимости CLI-режима из apt (через pkexec/sudo)."""

    missing = source_deps_missing()
    if not missing:
        return {"deps_ok": True, "deps_installed": []}

    packages = ["python3-httpx", "python3-h2", "python3-certifi", "python3-cryptography"]
    command = ["apt-get", "install", "-y", *packages]
    if util.which("pkexec"):
        argv = ["pkexec", *command]
    elif util.which("sudo"):
        argv = ["sudo", "-n", *command]
    else:
        return {
            "deps_ok": False,
            "deps_error": "нет pkexec/sudo: установите вручную: apt install " + " ".join(packages),
        }

    completed = util.run(argv, check=False, timeout=600)
    still_missing = source_deps_missing()
    return {
        "deps_ok": not still_missing,
        "deps_installed": [p for p in packages if p not in missing] if not still_missing else [],
        "deps_error": (completed.stderr or "").strip()[-500:] if still_missing else "",
    }


def set_tgws_mode(cfg: Config, mode: str) -> tuple[int, dict[str, object]]:
    """Переключить режим TgWsProxy: tray (X11) или cli (без графики)."""

    if mode not in tgws.MODES:
        return EXIT_USAGE, {"ok": False, "error": f"неизвестный режим: {mode}"}
    if mode == tgws.MODE_TRAY and not tgws.display_available():
        return EXIT_USAGE, {
            "ok": False,
            "error": "режим tray требует X11/DISPLAY; на этом хосте доступен только cli",
        }

    config_path, _created = tgws.ensure_config(
        create=True, port=cfg.tgws.port, host=cfg.tgws.host
    )
    if config_path is None:
        return EXIT_NOT_INSTALLED, {"ok": False, "error": "конфиг TgWsProxy не найден"}

    if mode == tgws.MODE_CLI:
        code, source_payload = install_tgws_source(_installed_version())
        if code != 0:
            return code, source_payload
        deps = ensure_source_deps()
        if not deps.get("deps_ok"):
            return EXIT_ERROR, {
                "ok": False,
                "error": str(deps.get("deps_error") or "нет python-зависимостей для CLI-режима"),
                "deps": deps,
            }

    try:
        mode_file = tgws.save_mode(mode)
        data, backup, changed = tgws.load_json_config(config_path), None, True
    except tgws.TgwsError as exc:
        return EXIT_ERROR, {"ok": False, "error": str(exc)}

    unit_path, unit_changed = tgws.install_user_unit(
        cfg.tgws.binary, mode=mode, data=data
    )
    # Перезапускаем службу: без этого продолжает работать старый процесс
    # (например, tray-бинарник при переключении на cli) — режим меняется
    # в конфиге, но не в жизни.
    util.run(["systemctl", "--user", "daemon-reload"], check=False, timeout=20)
    restarted = util.run(
        ["systemctl", "--user", "restart", tgws.UNIT_NAME], check=False, timeout=60
    )
    return EXIT_OK, {
        "ok": True,
        "mode": mode,
        "mode_file": str(mode_file),
        "changed": changed,
        "backup": str(backup) if backup else None,
        "unit": str(unit_path),
        "unit_changed": unit_changed,
        "restarted": restarted.returncode == 0,
        "restart_error": (restarted.stderr or "").strip(),
    }


def _installed_version() -> str:
    info = state.load_state()
    return info.tgws_version or "v1.11.4"


def enable_autostart(cfg: Config, *, start: bool = True) -> tuple[int, dict[str, object]]:
    """Включить автозапуск: tgwsproxy (user) + zapret (systemd, root через helper)."""

    enabled: dict[str, bool] = {}
    started: dict[str, bool] = {}

    # TgWsProxy — пользовательская служба: systemctl --user.
    # Юнит переустанавливаем: режим мог измениться (tray ↔ cli), а старые
    # версии писали его без DISPLAY и бинарник-трей падал при старте.
    config_path = tgws.find_config_path() or tgws.config_candidates()[0]
    mode = tgws.effective_mode(config_path if config_path.exists() else None)
    data = tgws.load_json_config(config_path) if config_path.exists() else {}
    if mode == tgws.MODE_CLI:
        install_tgws_source(_installed_version())
    tgws.install_user_unit(cfg.tgws.binary, systemctl=False, mode=mode, data=data)
    util.run(["systemctl", "--user", "daemon-reload"], check=False, timeout=20)
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

    # Guard: флаг в конфиге без запущенного таймера ничего не делает.
    timer = guard.ensure_timer(cfg.guard.enabled)

    return EXIT_OK, {
        "ok": all(enabled.values()) and all(started.values()),
        "enabled": enabled,
        "started": started,
        **timer,
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


# ---------------------------------------------------------------------------
# Hostlist zapret: свои списки, импорт, редактирование
# ---------------------------------------------------------------------------


def hostlist_path(cfg: Config) -> str | None:
    """Путь к hostlist, в который можно писать."""

    info = zapret.detect_zapret(cfg.zapret.service)
    return zapret.hostlist_target(info, cfg.zapret.hostlist)


def get_hostlist_entries(cfg: Config) -> tuple[int, dict[str, object]]:
    """Все домены hostlist с пометкой источника (cf/user/other)."""

    from . import hostlist as hostlist_mod

    path = hostlist_path(cfg)
    if not path:
        return EXIT_NOT_INSTALLED, {"ok": False, "error": "hostlist не определён"}
    entries = hostlist_mod.read_entries(path)
    counts = {"cf": 0, "user": 0, "other": 0}
    for entry in entries:
        counts[entry.source] = counts.get(entry.source, 0) + 1
    return EXIT_OK, {
        "ok": True,
        "hostlist": path,
        "counts": counts,
        "total": len(entries),
        "entries": [{"domain": e.domain, "source": e.source} for e in entries],
    }


def add_to_hostlist(
    cfg: Config, domains: list[str], *, restart: bool = True
) -> tuple[int, dict[str, object]]:
    """Добавить домены в hostlist (root через helper) с рестартом zapret."""

    path = hostlist_path(cfg)
    if not path:
        return EXIT_NOT_INSTALLED, {"ok": False, "error": "hostlist не определён"}
    args = ["--hostlist", path, "--timeout", "30"]
    for domain in domains:
        args.extend(["--domain", domain])
    if restart:
        args.append("--restart-on-change")
        info = zapret.detect_zapret(cfg.zapret.service)
        args.extend(["--service", info.service or cfg.zapret.service])
    try:
        return util.run_helper("hostlist.import", args, timeout=120)
    except util.NetfixError as exc:
        return EXIT_NO_RIGHTS, {"ok": False, "error": str(exc)}
    except util.CommandError as exc:
        return EXIT_ERROR, {"ok": False, "error": f"hostlist.import: {exc}"}


def import_hostlist(
    cfg: Config,
    *,
    url: str | None = None,
    list_file: str | None = None,
    restart: bool = True,
) -> tuple[int, dict[str, object]]:
    """Импортировать список доменов из URL или файла."""

    path = hostlist_path(cfg)
    if not path:
        return EXIT_NOT_INSTALLED, {"ok": False, "error": "hostlist не определён"}
    args = ["--hostlist", path, "--timeout", "60"]
    if url:
        args.extend(["--url", url])
    if list_file:
        args.extend(["--list-file", list_file])
    if restart:
        args.append("--restart-on-change")
        info = zapret.detect_zapret(cfg.zapret.service)
        args.extend(["--service", info.service or cfg.zapret.service])
    try:
        return util.run_helper("hostlist.import", args, timeout=300)
    except util.NetfixError as exc:
        return EXIT_NO_RIGHTS, {"ok": False, "error": str(exc)}
    except util.CommandError as exc:
        return EXIT_ERROR, {"ok": False, "error": f"hostlist.import: {exc}"}


def remove_from_hostlist(
    cfg: Config, domains: list[str], *, restart: bool = True
) -> tuple[int, dict[str, object]]:
    """Удалить домены из блоков LinFix с рестартом zapret."""

    path = hostlist_path(cfg)
    if not path:
        return EXIT_NOT_INSTALLED, {"ok": False, "error": "hostlist не определён"}
    args = ["--hostlist", path]
    for domain in domains:
        args.extend(["--domain", domain])
    if restart:
        args.append("--restart-on-change")
        info = zapret.detect_zapret(cfg.zapret.service)
        args.extend(["--service", info.service or cfg.zapret.service])
    try:
        return util.run_helper("hostlist.remove", args, timeout=120)
    except util.NetfixError as exc:
        return EXIT_NO_RIGHTS, {"ok": False, "error": str(exc)}
    except util.CommandError as exc:
        return EXIT_ERROR, {"ok": False, "error": f"hostlist.remove: {exc}"}


def hostlist_sources(cfg: Config) -> list[tuple[str, str]]:
    """Скрипты-источники списков, доступные в установленном zapret."""

    from . import hostlist as hostlist_mod

    info = zapret.detect_zapret(cfg.zapret.service)
    if not info.install_dir:
        return []
    return hostlist_mod.list_sources(Path(info.install_dir) / "ipset")


def load_hostlist_source(cfg: Config, script: str) -> tuple[int, dict[str, object]]:
    """Загрузить готовый список через штатный скрипт zapret (root)."""

    info = zapret.detect_zapret(cfg.zapret.service)
    directory = Path(info.install_dir) / "ipset" if info.install_dir else Path("/opt/zapret2/ipset")
    try:
        return util.run_helper(
            "hostlist.load_source",
            ["--script", script, "--ipset-dir", str(directory)],
            timeout=900,
        )
    except util.NetfixError as exc:
        return EXIT_NO_RIGHTS, {"ok": False, "error": str(exc)}
    except util.CommandError as exc:
        return EXIT_ERROR, {"ok": False, "error": f"hostlist.load_source: {exc}"}


def zapret_hostlist_arg_problems(cfg: Config) -> list[str]:
    """Строки конфига zapret, где hostlist передан без --hostlist."""

    info = zapret.detect_zapret(cfg.zapret.service)
    bare = list(zapret.find_bare_hostlist_args(info.cmdline))
    if bare:
        return bare
    return zapret.config_hostlist_problems()


def check_cf_path(limit: int = 3, timeout: float = 5.0) -> dict[str, object]:
    """Быстрая проверка CF-пути: сколько доменов отвечает по TLS."""

    try:
        results = cfdomains.test_domains(limit=limit, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — проверка не должна ронять UI
        return {"ok": 0, "total": 0, "error": str(exc)}
    ok = sum(1 for _host, alive, _detail in results if alive)
    return {"ok": ok, "total": len(results), "results": results}


def neutralize_zapret_hostlist_args(cfg: Config) -> tuple[int, dict[str, object]]:
    """Безопасно обезвредить профили zapret без ``--hostlist=``.

    Порядок: замерить CF-путь → закомментировать строки → перезапустить
    службу → замерить снова. Если стало хуже, вернуть конфиг из бэкапа.
    """

    info = zapret.detect_zapret(cfg.zapret.service)
    service = info.service or cfg.zapret.service
    base = Path(info.install_dir) if info.install_dir else Path("/opt/zapret2")
    config_path = base / "config"

    before = check_cf_path()
    try:
        code, payload = util.run_helper(
            "zapret.neutralize_hostlist_args", ["--config-path", str(config_path)], timeout=120
        )
    except util.NetfixError as exc:
        return EXIT_NO_RIGHTS, {"ok": False, "error": str(exc)}
    except util.CommandError as exc:
        return EXIT_ERROR, {"ok": False, "error": f"правка конфига: {exc}"}

    payload["before"] = before
    if code != 0 or not payload.get("changed"):
        return code, payload

    restart = util.run(["systemctl", "restart", service], check=False, timeout=60)
    payload["restarted"] = restart.returncode == 0
    after = check_cf_path()
    payload["after"] = after

    before_ok = int(before.get("ok") or 0)
    after_ok = int(after.get("ok") or 0)
    if after_ok < before_ok:
        # Регресс — откатываем автоматически, не оставляя пользователя
        # со сломанной сетью.
        backup = payload.get("backup")
        if backup:
            try:
                restore_code, _restore = util.run_helper(
                    "zapret.restore_config",
                    ["--backup", str(backup), "--config-path", str(config_path)],
                    timeout=120,
                )
            except (util.NetfixError, util.CommandError) as exc:
                payload["ok"] = False
                payload["rolled_back"] = False
                payload["error"] = f"ухудшилось, а откат не удался: {exc}"
                return EXIT_ERROR, payload
            util.run(["systemctl", "restart", service], check=False, timeout=60)
            payload["rolled_back"] = restore_code == 0
            payload["ok"] = False
            payload["error"] = (
                f"после правки CF-путь стал хуже ({before_ok}/{before.get('total')} → "
                f"{after_ok}/{after.get('total')}); конфиг возвращён назад"
            )
            return EXIT_CHECK_FAILED, payload

    payload["ok"] = True
    payload["improved"] = after_ok > before_ok
    return EXIT_OK, payload


def add_to_cf_domains(cfg: Config, domains: list[str], *, restart: bool = True) -> tuple[int, dict[str, object]]:
    """Добавить домены в блок CF-доменов TgWsProxy в hostlist."""

    path = hostlist_path(cfg)
    if not path:
        return EXIT_NOT_INSTALLED, {"ok": False, "error": "hostlist не определён"}
    args = ["--hostlist", path, "--section", "cf", "--timeout", "30"]
    for domain in domains:
        args.extend(["--domain", domain])
    if restart:
        args.append("--restart-on-change")
        info = zapret.detect_zapret(cfg.zapret.service)
        args.extend(["--service", info.service or cfg.zapret.service])
    try:
        return util.run_helper("hostlist.import", args, timeout=120)
    except util.NetfixError as exc:
        return EXIT_NO_RIGHTS, {"ok": False, "error": str(exc)}
    except util.CommandError as exc:
        return EXIT_ERROR, {"ok": False, "error": f"hostlist.import: {exc}"}


def update_user_rotation_domains(
    cfg: Config,
    add: list[str] | None = None,
    remove: list[str] | None = None,
) -> tuple[int, dict[str, object]]:
    """Изменить пользовательский список ротации TgWsProxy.

    Меняется и сохранённый список (`tgws-rotation.json`), и конфиг прокси,
    если сейчас активен режим пользовательских доменов. В режиме GitHub
    список только сохраняется — чтобы не переключать прокси неявно.
    """

    path = tgws.find_config_path()
    data: dict[str, object] = {}
    if path is not None:
        try:
            data = tgws.load_json_config(path)
        except tgws.TgwsError as exc:
            return EXIT_ERROR, {"ok": False, "error": str(exc)}

    current = _rotation_list(data, "cfproxy_user_domain") or tgws.load_rotation_domains()
    target = list(current)

    for domain in add or []:
        item = domain.strip().lower()
        if item and item not in target:
            target.append(item)
    for domain in remove or []:
        item = domain.strip().lower()
        if item in target:
            target.remove(item)

    if not target:
        return EXIT_USAGE, {
            "ok": False,
            "error": "пользовательский список не может быть пустым",
        }
    if target == current:
        return EXIT_OK, {"ok": True, "changed": False, "domains": target}

    try:
        tgws.save_rotation_domains(target)
    except tgws.TgwsError as exc:
        return EXIT_ERROR, {"ok": False, "error": str(exc)}

    active_mode = ROTATION_USER if _rotation_list(data, "cfproxy_user_domain") else ROTATION_GITHUB
    if active_mode == ROTATION_USER:
        code, payload = set_tgws_config({"cfproxy_user_domain": target})
        if code != EXIT_OK or payload.get("ok") is False:
            return code, payload
        updated = tgws.load_json_config(path) if path is not None else {}
        try:
            tgws.install_user_unit(
                cfg.tgws.binary, mode=tgws.effective_mode(path), data=updated
            )
        except tgws.TgwsError as exc:
            return EXIT_ERROR, {"ok": False, "error": str(exc)}
        util.run(["systemctl", "--user", "daemon-reload"], check=False, timeout=20)
        restarted = util.run(
            ["systemctl", "--user", "restart", tgws.UNIT_NAME], check=False, timeout=60
        )
    else:
        restarted = None

    return EXIT_OK, {
        "ok": True,
        "changed": True,
        "domains": target,
        "active_mode": active_mode,
        "saved_only": active_mode == ROTATION_GITHUB,
        "restarted": None if restarted is None else restarted.returncode == 0,
    }


def get_cf_domains(cfg: Config) -> tuple[int, dict[str, object]]:
    """CF-домены для hostlist (state или блок hostlist) + пользовательская ротация.

    ``domains`` — список, который показывается в таблице по умолчанию
    (исторический источник hostlist). Отдельно возвращаются:

    - ``user_domains`` — то, что реально прописано в конфиге TgWsProxy;
    - ``saved_domains`` — сохранённый пользовательский список ротации;
    - ``active_mode`` — какой источник сейчас у прокси.
    """

    from . import hostlist as hostlist_mod

    state_info = state.load_state()
    path = hostlist_path(cfg)
    in_hostlist = hostlist_mod.cf_domains(path) if path else []
    stored = list(state_info.domains)
    domains = stored or in_hostlist
    present = set(in_hostlist)
    missing = [domain for domain in domains if domain not in present]

    config_domains: list[str] = []
    config_path = tgws.find_config_path()
    if config_path is not None:
        try:
            config_domains = _rotation_list(
                tgws.load_json_config(config_path), "cfproxy_user_domain"
            )
        except tgws.TgwsError:
            config_domains = []

    saved = tgws.load_rotation_domains()
    active_mode = ROTATION_USER if config_domains else ROTATION_GITHUB
    return EXIT_OK, {
        "ok": True,
        "hostlist": path,
        "source": "state" if stored else "hostlist",
        "updated_at": state_info.domains_updated_at,
        "domains": domains,
        "missing": missing,
        "in_hostlist": len(present),
        "user_domains": config_domains,
        "saved_domains": saved,
        "active_mode": active_mode,
    }


# ---------------------------------------------------------------------------
# Подбор стратегии desync (blockcheck2)
# ---------------------------------------------------------------------------


def blockcheck_status(cfg: Config) -> tuple[bool, str]:
    """Можно ли запускать подбор стратегии прямо сейчас."""

    from . import blockcheck

    return blockcheck.check_readiness(cfg)


def set_blockcheck_domains(domains: list[str]) -> tuple[int, dict[str, object]]:
    """Сохранить список доменов для подбора стратегий (через helper)."""

    cleaned = [item.strip().lower() for item in domains if item and item.strip()]
    if not cleaned:
        return EXIT_USAGE, {"ok": False, "error": "нужен хотя бы один домен"}
    args: list[str] = []
    for domain in cleaned:
        args.extend(["--domain", domain])
    try:
        return util.run_helper("config.blockcheck", args, timeout=60)
    except util.NetfixError as exc:
        return EXIT_NO_RIGHTS, {"ok": False, "error": str(exc)}
    except util.CommandError as exc:
        return EXIT_ERROR, {"ok": False, "error": f"config.blockcheck: {exc}"}


def apply_strategy_via_helper(
    cfg: Config, desync: list[str]
) -> tuple[int, dict[str, object]]:
    """Применить стратегию через helper: правка конфига требует root."""

    import json as _json

    try:
        code, payload = util.run_helper(
            "zapret.apply_strategy",
            ["--desync", _json.dumps(desync, ensure_ascii=False)],
            timeout=300,
        )
    except util.NetfixError as exc:
        return EXIT_NO_RIGHTS, {"ok": False, "error": str(exc)}
    except util.CommandError as exc:
        return EXIT_ERROR, {"ok": False, "error": f"применение стратегии: {exc}"}
    return code, payload


def apply_strategy(
    cfg: Config, desync: list[str], *, hostlist: str | None = None
) -> tuple[int, dict[str, object]]:
    """Применить стратегию к zapret с резервной копией конфига (root).

    В отличие от проверки, здесь профиль **остаётся** применённым: это
    осознанное действие пользователя. Бэкап делается до правки, чтобы откат
    был возможен одной командой.
    """

    from . import applystrategy

    if not desync:
        return EXIT_USAGE, {"ok": False, "error": "пустая стратегия"}

    info = zapret.detect_zapret(cfg.zapret.service)
    if not info.install_dir:
        return EXIT_NOT_INSTALLED, {"ok": False, "error": "zapret не установлен"}
    service = info.service or cfg.zapret.service
    config_path = Path(info.install_dir) / "config"

    target = hostlist or applystrategy.hostlist_for_test(config_path)
    if not target:
        return EXIT_CHECK_FAILED, {
            "ok": False,
            "error": "не найден hostlist: некуда направить профиль",
        }

    try:
        line = applystrategy.build_profile_line(desync, hostlist=target)
    except applystrategy.ApplyError as exc:
        return EXIT_USAGE, {"ok": False, "error": str(exc)}

    backup = Path(str(config_path) + ".bak-linfix-apply")
    try:
        shutil.copy2(config_path, backup)
    except OSError as exc:
        return EXIT_NO_RIGHTS, {"ok": False, "error": f"не удалось сделать бэкап: {exc}"}

    try:
        applystrategy.replace_profile_block(line, config_path)
    except (applystrategy.ApplyError, OSError) as exc:
        return EXIT_ERROR, {"ok": False, "error": str(exc)}

    if not applystrategy.restart(service):
        # Не оставляем систему с нерабочим профилем.
        try:
            shutil.copy2(backup, config_path)
        except OSError:
            pass
        applystrategy.restart(service)
        return EXIT_CHECK_FAILED, {
            "ok": False,
            "error": f"{service} не поднялся — конфиг возвращён из бэкапа",
        }

    return EXIT_OK, {
        "ok": True,
        "applied": True,
        "service": service,
        "hostlist": target,
        "line": line,
        "backup": str(backup),
    }


def verify_candidates_via_helper(
    cfg: Config, strategies: list[dict[str, object]], *, limit: int = 4
) -> tuple[int, dict[str, object]]:
    """То же, но через helper: изменение конфига требует root."""

    import json as _json

    try:
        code, payload = util.run_helper(
            "zapret.verify_candidates",
            [
                "--candidates",
                _json.dumps(strategies, ensure_ascii=False),
                "--limit",
                str(limit),
            ],
            timeout=600,
        )
    except util.NetfixError as exc:
        return EXIT_NO_RIGHTS, {"ok": False, "error": str(exc)}
    except util.CommandError as exc:
        return EXIT_ERROR, {"ok": False, "error": f"проверка кандидатов: {exc}"}
    return code, payload


def verify_candidates(
    cfg: Config,
    strategies: list[dict[str, object]],
    *,
    limit: int = 4,
) -> tuple[int, dict[str, object]]:
    """Проверить кандидатов телом 256 КБ, применяя каждого к zapret.

    `blockcheck2` проверяет одноразовым GET: он пропускает стратегии,
    которые открывают заголовки, но ломают тело (классика — `tcpseg`).
    Здесь каждый кандидат реально применяется к zapret, проверяется H2-телом
    и откатывается. Откат гарантирован даже при ошибке.

    Нужен root: изменение конфига и перезапуск службы.
    """

    from . import applystrategy, cfdomains, cfprofile

    if not strategies:
        return EXIT_USAGE, {"ok": False, "error": "нет кандидатов для проверки"}

    domains = list(state.load_state().domains or cfdomains.FALLBACK_DOMAINS)[:2]
    if not domains:
        return EXIT_USAGE, {"ok": False, "error": "нет CF-доменов для проверки"}
    hosts = [f"kws2.{domain}" for domain in domains]

    results: list[dict[str, object]] = []
    for item in strategies[:limit]:
        desync = list(item.get("desync") or [])  # type: ignore[arg-type]
        if not desync:
            args = str(item.get("args") or "")
            desync = [token for token in args.split() if "--lua-desync=" in token]
        if not desync:
            continue

        checked: dict[str, object] = {**item, "desync": desync}
        # cfg.zapret.service по умолчанию «zapret», а установлена может быть
        # «zapret2»: реальное имя определяет detect_zapret.
        info = zapret.detect_zapret(cfg.zapret.service)
        service = info.service or cfg.zapret.service
        try:
            with applystrategy.applied(desync, service=service):
                probed = cfprofile.probe_hosts_parallel(hosts, timeout=8.0)
            ok_count = sum(1 for ok, _ms in probed.values() if ok)
            checked.update(
                {
                    "body_ok": ok_count == len(hosts),
                    "body_ok_count": ok_count,
                    "body_total": len(hosts),
                    "applied": True,
                }
            )
        except (applystrategy.ApplyError, util.NetfixError) as exc:
            checked.update({"body_ok": False, "applied": False, "error": str(exc)})
        results.append(checked)

    results.sort(
        key=lambda x: (bool(x.get("body_ok")), int(x.get("body_ok_count") or 0)),
        reverse=True,
    )
    best = next((item for item in results if item.get("body_ok")), None)
    return EXIT_OK, {
        "ok": True,
        "checked": results,
        "best": best,
        "count": len(results),
    }


def pick_strategy(
    cfg: Config,
    *,
    domains: list[str] | None = None,
    level: str = "quick",
    timeout: float = 2100.0,
) -> tuple[int, dict[str, object]]:
    """Подобрать рабочую стратегию desync через blockcheck2 (root через helper).

    blockcheck2 сам просит root и запускает sudo, а в GUI терминала нет —
    поэтому запускаем его из helper, который уже root. Заодно это один
    запрос пароля вместо двух.
    """

    info = zapret.detect_zapret(cfg.zapret.service)
    if not info.install_dir:
        return EXIT_NOT_INSTALLED, {"ok": False, "error": "zapret не установлен"}

    args = [
        "--service",
        info.service or cfg.zapret.service,
        "--install-dir",
        str(info.install_dir),
        "--level",
        level,
        "--timeout",
        str(timeout),
    ]
    args.extend(["--fast", "on" if cfg.blockcheck.fast else "off"])
    selected = list(domains) if domains else list(cfg.blockcheck.effective_domains)
    for domain in selected:
        args.extend(["--domain", domain])

    was_active = info.active
    try:
        code, payload = util.run_helper("zapret.blockcheck", args, timeout=timeout + 300)
    except util.NetfixError as exc:
        _ensure_zapret_running(cfg, was_active)
        return EXIT_NO_RIGHTS, {"ok": False, "error": str(exc)}
    except util.CommandError as exc:
        _ensure_zapret_running(cfg, was_active)
        return EXIT_ERROR, {"ok": False, "error": f"подбор стратегии: {exc}"}

    # Страховка: helper мог быть убит по таймауту и не успеть вернуть службу.
    if was_active:
        _ensure_zapret_running(cfg, was_active)

    if code != 0:
        return code, payload

    best = payload.get("best")
    note = ""
    if not best and payload.get("works_without_bypass"):
        note = (
            "эти домены открываются без обхода — у вашего провайдера они не "
            "блокируются, подбирать стратегию не нужно"
        )
    return EXIT_OK, {
        **payload,
        "ok": True,
        "found": bool(best),
        "note": note,
        "error": note if not best and not payload.get("error") else payload.get("error", ""),
    }


# ---------------------------------------------------------------------------
# Hostlist zapret: свои списки, импорт, редактирование
# ---------------------------------------------------------------------------


def hostlist_path(cfg: Config) -> str | None:
    """Путь к hostlist, в который можно писать."""

    info = zapret.detect_zapret(cfg.zapret.service)
    return zapret.hostlist_target(info, cfg.zapret.hostlist)


def get_hostlist_entries(cfg: Config) -> tuple[int, dict[str, object]]:
    """Все домены hostlist с пометкой источника (cf/user/other)."""

    from . import hostlist as hostlist_mod

    path = hostlist_path(cfg)
    if not path:
        return EXIT_NOT_INSTALLED, {"ok": False, "error": "hostlist не определён"}
    entries = hostlist_mod.read_entries(path)
    counts = {"cf": 0, "user": 0, "other": 0}
    for entry in entries:
        counts[entry.source] = counts.get(entry.source, 0) + 1
    return EXIT_OK, {
        "ok": True,
        "hostlist": path,
        "counts": counts,
        "total": len(entries),
        "entries": [{"domain": e.domain, "source": e.source} for e in entries],
    }


def add_to_hostlist(
    cfg: Config, domains: list[str], *, restart: bool = True
) -> tuple[int, dict[str, object]]:
    """Добавить домены в hostlist (root через helper) с рестартом zapret."""

    path = hostlist_path(cfg)
    if not path:
        return EXIT_NOT_INSTALLED, {"ok": False, "error": "hostlist не определён"}
    args = ["--hostlist", path, "--timeout", "30"]
    for domain in domains:
        args.extend(["--domain", domain])
    if restart:
        args.append("--restart-on-change")
        info = zapret.detect_zapret(cfg.zapret.service)
        args.extend(["--service", info.service or cfg.zapret.service])
    try:
        return util.run_helper("hostlist.import", args, timeout=120)
    except util.NetfixError as exc:
        return EXIT_NO_RIGHTS, {"ok": False, "error": str(exc)}
    except util.CommandError as exc:
        return EXIT_ERROR, {"ok": False, "error": f"hostlist.import: {exc}"}


def import_hostlist(
    cfg: Config,
    *,
    url: str | None = None,
    list_file: str | None = None,
    restart: bool = True,
) -> tuple[int, dict[str, object]]:
    """Импортировать список доменов из URL или файла."""

    path = hostlist_path(cfg)
    if not path:
        return EXIT_NOT_INSTALLED, {"ok": False, "error": "hostlist не определён"}
    args = ["--hostlist", path, "--timeout", "60"]
    if url:
        args.extend(["--url", url])
    if list_file:
        args.extend(["--list-file", list_file])
    if restart:
        args.append("--restart-on-change")
        info = zapret.detect_zapret(cfg.zapret.service)
        args.extend(["--service", info.service or cfg.zapret.service])
    try:
        return util.run_helper("hostlist.import", args, timeout=300)
    except util.NetfixError as exc:
        return EXIT_NO_RIGHTS, {"ok": False, "error": str(exc)}
    except util.CommandError as exc:
        return EXIT_ERROR, {"ok": False, "error": f"hostlist.import: {exc}"}


def remove_from_hostlist(
    cfg: Config, domains: list[str], *, restart: bool = True
) -> tuple[int, dict[str, object]]:
    """Удалить домены из блоков LinFix с рестартом zapret."""

    path = hostlist_path(cfg)
    if not path:
        return EXIT_NOT_INSTALLED, {"ok": False, "error": "hostlist не определён"}
    args = ["--hostlist", path]
    for domain in domains:
        args.extend(["--domain", domain])
    if restart:
        args.append("--restart-on-change")
        info = zapret.detect_zapret(cfg.zapret.service)
        args.extend(["--service", info.service or cfg.zapret.service])
    try:
        return util.run_helper("hostlist.remove", args, timeout=120)
    except util.NetfixError as exc:
        return EXIT_NO_RIGHTS, {"ok": False, "error": str(exc)}
    except util.CommandError as exc:
        return EXIT_ERROR, {"ok": False, "error": f"hostlist.remove: {exc}"}


def hostlist_sources(cfg: Config) -> list[tuple[str, str]]:
    """Скрипты-источники списков, доступные в установленном zapret."""

    from . import hostlist as hostlist_mod

    info = zapret.detect_zapret(cfg.zapret.service)
    if not info.install_dir:
        return []
    return hostlist_mod.list_sources(Path(info.install_dir) / "ipset")


def load_hostlist_source(cfg: Config, script: str) -> tuple[int, dict[str, object]]:
    """Загрузить готовый список через штатный скрипт zapret (root)."""

    info = zapret.detect_zapret(cfg.zapret.service)
    directory = Path(info.install_dir) / "ipset" if info.install_dir else Path("/opt/zapret2/ipset")
    try:
        return util.run_helper(
            "hostlist.load_source",
            ["--script", script, "--ipset-dir", str(directory)],
            timeout=900,
        )
    except util.NetfixError as exc:
        return EXIT_NO_RIGHTS, {"ok": False, "error": str(exc)}
    except util.CommandError as exc:
        return EXIT_ERROR, {"ok": False, "error": f"hostlist.load_source: {exc}"}


def zapret_hostlist_arg_problems(cfg: Config) -> list[str]:
    """Строки конфига zapret, где hostlist передан без --hostlist."""

    info = zapret.detect_zapret(cfg.zapret.service)
    bare = list(zapret.find_bare_hostlist_args(info.cmdline))
    if bare:
        return bare
    return zapret.config_hostlist_problems()


def check_cf_path(limit: int = 3, timeout: float = 5.0) -> dict[str, object]:
    """Быстрая проверка CF-пути: сколько доменов отвечает по TLS."""

    try:
        results = cfdomains.test_domains(limit=limit, timeout=timeout)
    except Exception as exc:  # noqa: BLE001 — проверка не должна ронять UI
        return {"ok": 0, "total": 0, "error": str(exc)}
    ok = sum(1 for _host, alive, _detail in results if alive)
    return {"ok": ok, "total": len(results), "results": results}


def neutralize_zapret_hostlist_args(cfg: Config) -> tuple[int, dict[str, object]]:
    """Безопасно обезвредить профили zapret без ``--hostlist=``.

    Порядок: замерить CF-путь → закомментировать строки → перезапустить
    службу → замерить снова. Если стало хуже, вернуть конфиг из бэкапа.
    """

    info = zapret.detect_zapret(cfg.zapret.service)
    service = info.service or cfg.zapret.service
    base = Path(info.install_dir) if info.install_dir else Path("/opt/zapret2")
    config_path = base / "config"

    before = check_cf_path()
    try:
        code, payload = util.run_helper(
            "zapret.neutralize_hostlist_args", ["--config-path", str(config_path)], timeout=120
        )
    except util.NetfixError as exc:
        return EXIT_NO_RIGHTS, {"ok": False, "error": str(exc)}
    except util.CommandError as exc:
        return EXIT_ERROR, {"ok": False, "error": f"правка конфига: {exc}"}

    payload["before"] = before
    if code != 0 or not payload.get("changed"):
        return code, payload

    restart = util.run(["systemctl", "restart", service], check=False, timeout=60)
    payload["restarted"] = restart.returncode == 0
    after = check_cf_path()
    payload["after"] = after

    before_ok = int(before.get("ok") or 0)
    after_ok = int(after.get("ok") or 0)
    if after_ok < before_ok:
        # Регресс — откатываем автоматически, не оставляя пользователя
        # со сломанной сетью.
        backup = payload.get("backup")
        if backup:
            try:
                restore_code, _restore = util.run_helper(
                    "zapret.restore_config",
                    ["--backup", str(backup), "--config-path", str(config_path)],
                    timeout=120,
                )
            except (util.NetfixError, util.CommandError) as exc:
                payload["ok"] = False
                payload["rolled_back"] = False
                payload["error"] = f"ухудшилось, а откат не удался: {exc}"
                return EXIT_ERROR, payload
            util.run(["systemctl", "restart", service], check=False, timeout=60)
            payload["rolled_back"] = restore_code == 0
            payload["ok"] = False
            payload["error"] = (
                f"после правки CF-путь стал хуже ({before_ok}/{before.get('total')} → "
                f"{after_ok}/{after.get('total')}); конфиг возвращён назад"
            )
            return EXIT_CHECK_FAILED, payload

    payload["ok"] = True
    payload["improved"] = after_ok > before_ok
    return EXIT_OK, payload


def add_to_cf_domains(cfg: Config, domains: list[str], *, restart: bool = True) -> tuple[int, dict[str, object]]:
    """Добавить домены в блок CF-доменов TgWsProxy в hostlist."""

    path = hostlist_path(cfg)
    if not path:
        return EXIT_NOT_INSTALLED, {"ok": False, "error": "hostlist не определён"}
    args = ["--hostlist", path, "--section", "cf", "--timeout", "30"]
    for domain in domains:
        args.extend(["--domain", domain])
    if restart:
        args.append("--restart-on-change")
        info = zapret.detect_zapret(cfg.zapret.service)
        args.extend(["--service", info.service or cfg.zapret.service])
    try:
        return util.run_helper("hostlist.import", args, timeout=120)
    except util.NetfixError as exc:
        return EXIT_NO_RIGHTS, {"ok": False, "error": str(exc)}
    except util.CommandError as exc:
        return EXIT_ERROR, {"ok": False, "error": f"hostlist.import: {exc}"}


def _ensure_zapret_running(cfg: Config, was_active: bool) -> None:
    """Если zapret был включён до операции, вернуть его в работу."""

    if not was_active:
        return
    # Пока идёт подбор, поднимать zapret нельзя: он испортит замеры.
    # Если helper убили, маркер мёртв и страховка сработает как обычно.
    if blockcheck.running() or blockcheck.stray_processes():
        return
    info = zapret.detect_zapret(cfg.zapret.service)
    if info.active:
        return
    service = info.service or cfg.zapret.service
    try:
        util.run_helper("zapret.restart", ["--service", service], timeout=150)
    except (util.NetfixError, util.CommandError):
        # Молча: это страховка, основной результат уже сформирован.
        pass
