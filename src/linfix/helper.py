"""Привилегированный one-shot helper.

Принимает только фиксированный набор действий и валидированные аргументы.
Никакого ``shell=True``; все пути канонизируются и проверяются на принадлежность
разрешённым директориям (SPEC 5.2 и 15.2).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
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
#: Единственный sysctl, который helper согласен менять.
ALLOWED_SYSCTLS = {"net.ipv4.tcp_mtu_probing"}
DEFAULT_ZAPRET2_GIT_URL = "https://github.com/bol-van/zapret2"
ALLOWED_ACTIONS = {
    "config.guard",
    "config.blockcheck",
    "domains.apply",
    "mtu.set",
    "zapret.neutralize_hostlist_args",
    "zapret.restore_config",
    "sysctl.set",
    "hostlist.import",
    "hostlist.load_source",
    "hostlist.remove",
    "domains.merge",
    "profile.apply",
    "profile.clear",
    "profile.set",
    "tgws.install",
    "zapret.install_easy",
    "zapret.set_enabled",
    "zapret.stop",
    "zapret.blockcheck",
    "zapret.blockcheck_stop",
    "zapret.restart",
    "net.clear_ipv6_blackholes",
    "zapret.save_profile",
    "zapret.restore_profile",
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


def validate_enabled(raw: str) -> str:
    if raw not in {"on", "off"}:
        raise HelperError(f"недопустимое значение: {raw} (ожидается on|off)")
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


def action_config_blockcheck(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    """Записать список доменов для подбора стратегий в конфиг LinFix."""

    from .config import set_blockcheck_domains

    domains = [item for item in (args.domain or []) if item and item.strip()]
    if not domains:
        raise HelperError("config.blockcheck: нужен хотя бы один --domain")

    target = validate_path(args.config or str(DEFAULT_CONFIG_PATH), must_exist=False)
    existed = target.exists()
    text = ""
    if existed:
        with target.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            text = handle.read()

    try:
        new_text = set_blockcheck_domains(text, domains)
    except ValueError as exc:
        raise HelperError(str(exc)) from exc

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
        if tuple(updated.blockcheck.domains) != tuple(
            item.strip().lower() for item in domains
        ):
            if backup is not None:
                util.restore_file(backup, target)
            raise HelperError("не удалось изменить [blockcheck] domains")

    return 0, {
        "ok": True,
        "changed": changed,
        "path": str(target),
        "backup": str(backup) if backup else None,
        "domains": [item.strip().lower() for item in domains],
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


def action_zapret_stop(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    if not args.service:
        raise HelperError("zapret.stop: требуется --service")
    service = validate_service(args.service)

    completed = util.run(["systemctl", "stop", service], check=False, timeout=20)
    status_cp = util.run(["systemctl", "is-active", service], check=False, timeout=10)
    active = (status_cp.stdout or "").strip() == "active"

    journal = ""
    if completed.returncode != 0 or active:
        tail = util.run(["journalctl", "-u", service, "-n", "50", "--no-pager"], check=False, timeout=15)
        journal = (tail.stdout or "")[-4000:]

    payload = {
        "ok": not active and completed.returncode == 0,
        "service": service,
        "state": (status_cp.stdout or "").strip() or "unknown",
        "returncode": completed.returncode,
        "journal": journal,
    }
    return (0 if payload["ok"] else 5), payload


#: Что обновляем из архива. Конфиг и наши списки сюда НЕ входят:
#: их нельзя затирать — там профили CF/rutracker и домены пользователя.
ARCHIVE_COPY = (
    "binaries",
    "lua",
    "blockcheck2.sh",
    "blockcheck2.d",
    "common",
    "init.d",
    "ip2net",
    "mdig",
    "nfq2",
    "files",
    "docs",
    "Makefile",
    "install_bin.sh",
    "install_easy.sh",
)

ZAPRET_RELEASE_ASSET = (
    "https://github.com/bol-van/zapret2/releases/latest/download/{asset}"
)
ZAPRET_VERSION_FILE = Path("/var/lib/linfix/zapret-version")


def _update_zapret_from_release(
    service: str, target: Path, args: argparse.Namespace
) -> tuple[int, dict[str, object]]:
    """Обновить zapret2 из релизного архива GitHub.

    Почему релиз, а не git: в git-архиве zapret2 бинарников нет (только
    ``binaries/readme.txt`` — «бинарники только в релизах»). Релизный
    ``zapret2-vX.Y.Z.tar.gz`` содержит готовые `nfqws2`, `ip2net`, `mdig`.

    Конфиг и наши списки не копируются: там профили CF/rutracker и домены
    пользователя.
    """

    import tarfile
    import tempfile
    import urllib.request

    asset = getattr(args, "asset", "") or ""
    url = getattr(args, "asset_url", "") or (
        ZAPRET_RELEASE_ASSET.format(asset=asset) if asset else ""
    )
    if not url:
        return 5, {"ok": False, "error": "не указан релизный архив zapret2"}

    tmp = Path(tempfile.mkdtemp(prefix="zapret-update-"))
    try:
        archive = tmp / "zapret.tar.gz"
        request = urllib.request.Request(url, headers={"User-Agent": "linfix"})
        try:
            with urllib.request.urlopen(request, timeout=args.timeout) as response:
                archive.write_bytes(response.read())
        except Exception as exc:  # noqa: BLE001
            return 5, {"ok": False, "error": f"не удалось скачать архив zapret2: {exc}"}

        expected = getattr(args, "asset_sha256", "") or ""
        if expected:
            import hashlib

            digest = hashlib.sha256(archive.read_bytes()).hexdigest()
            if digest != expected.lower():
                return 5, {
                    "ok": False,
                    "error": f"контрольная сумма не совпала: {digest[:16]}…",
                }

        try:
            with tarfile.open(archive) as tar:
                tar.extractall(tmp, filter="data")
        except Exception as exc:  # noqa: BLE001
            return 5, {"ok": False, "error": f"не удалось распаковать архив: {exc}"}

        roots = [item for item in tmp.iterdir() if item.is_dir()]
        if not roots:
            return 5, {"ok": False, "error": "в архиве нет каталога с исходниками"}
        source_root = roots[0]

        # Бинарники работают, и ядро не даёт перезаписать занятый файл
        # («Text file busy»). Останавливаем службу на время замены.
        was_active = (
            util.run(["systemctl", "is-active", service], check=False, timeout=10).stdout or ""
        ).strip() == "active"
        if was_active:
            util.run(["systemctl", "stop", service], check=False, timeout=60)

        copied: list[str] = []
        try:
            for name in ARCHIVE_COPY:
                src = source_root / name
                if not src.exists():
                    continue
                dst = target / name
                if src.is_dir():
                    shutil.copytree(src, dst, dirs_exist_ok=True)
                else:
                    dst.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(src, dst)
                copied.append(name)
        except OSError as exc:
            # Не оставляем zapret выключенным, если копирование сорвалось.
            if was_active:
                util.run(["systemctl", "start", service], check=False, timeout=60)
            return 5, {"ok": False, "error": f"не удалось заменить файлы: {exc}"}

        installed = util.run(
            ["sh", str(target / "install_bin.sh")],
            check=False,
            timeout=180,
            env={
                **os.environ,
                "ZAPRET_BASE": str(target),
                "ZAPRET_TARGET": str(target),
                "ZAPRET_TARGET_RW": str(target),
            },
        )
        output = ((installed.stdout or "") + (installed.stderr or "")).strip()

        version = getattr(args, "zapret_version", "") or ""
        if version:
            try:
                ZAPRET_VERSION_FILE.parent.mkdir(parents=True, exist_ok=True)
                ZAPRET_VERSION_FILE.write_text(version + "\n", encoding="utf-8")
            except OSError as exc:
                return 5, {"ok": False, "error": f"не удалось записать версию: {exc}"}

        restarted = util.run(["systemctl", "restart", service], check=False, timeout=60)
        state = util.run(["systemctl", "is-active", service], check=False, timeout=10)
        active = (state.stdout or "").strip() == "active"

        payload: dict[str, object] = {
            "ok": restarted.returncode == 0 and active,
            "service": service,
            "target": str(target),
            "method": "release",
            "version": version,
            "copied": copied,
            "active": active,
            "output": output[-2000:],
        }
        if not payload["ok"]:
            payload["error"] = f"{service} не поднялся после обновления"
        return (0 if payload["ok"] else 5), payload
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _update_zapret_sources(
    service: str, target: Path, url: str, args: argparse.Namespace
) -> tuple[int, dict[str, object]]:
    """Обновить исходники и бинарники zapret, не трогая config.

    ``install_easy.sh`` для уже установленного zapret запускать нельзя: он
    интерактивен и перезапишет ``config``. Поэтому обновляем только то, что
    относится к коду: подтягиваем git-репозиторий, копируем бинарники и
    перезапускаем службу. Конфиг остаётся наш, а бэкап на всякий случай
    делает вызывающая сторона.
    """

    if not (target / ".git").is_dir():
        # Установка сделана копированием (без git) — берём релизный архив.
        return _update_zapret_from_release(service, target, args)

    # Обновляем исходники. --depth 1 не даёт сделать fetch полноценно,
    # поэтому при неудаче пересоздаём мелкий клон.
    before = util.run(
        ["git", "-C", str(target), "rev-parse", "--short", "HEAD"],
        check=False,
        timeout=20,
    )
    before_sha = (before.stdout or "").strip()

    fetched = util.run(
        ["git", "-C", str(target), "fetch", "--depth", "1", "origin"],
        check=False,
        timeout=args.timeout,
    )
    if fetched.returncode != 0:
        detail = ((fetched.stderr or "") + (fetched.stdout or "")).strip()[-800:]
        return 5, {"ok": False, "error": f"git fetch не удался: {detail or 'ошибка'}"}

    reset = util.run(
        ["git", "-C", str(target), "reset", "--hard", "FETCH_HEAD"],
        check=False,
        timeout=120,
    )
    if reset.returncode != 0:
        detail = ((reset.stderr or "") + (reset.stdout or "")).strip()[-800:]
        return 5, {"ok": False, "error": f"git reset не удался: {detail or 'ошибка'}"}

    after = util.run(
        ["git", "-C", str(target), "rev-parse", "--short", "HEAD"],
        check=False,
        timeout=20,
    )
    after_sha = (after.stdout or "").strip()

    # install_bin.sh кладёт свежие ip2net/mdig/nfqws2 из binaries/ в корень.
    installed = util.run(
        ["sh", str(target / "install_bin.sh")],
        check=False,
        timeout=180,
        env={
            **os.environ,
            "ZAPRET_BASE": str(target),
            "ZAPRET_TARGET": str(target),
            "ZAPRET_TARGET_RW": str(target),
        },
    )
    output = ((installed.stdout or "") + (installed.stderr or "")).strip()
    if installed.returncode != 0:
        return 5, {
            "ok": False,
            "error": f"install_bin.sh завершился с кодом {installed.returncode}",
            "output": output[-2000:],
        }

    restarted = util.run(["systemctl", "restart", service], check=False, timeout=60)
    state = util.run(["systemctl", "is-active", service], check=False, timeout=10)
    active = (state.stdout or "").strip() == "active"

    payload: dict[str, object] = {
        "ok": restarted.returncode == 0 and active,
        "service": service,
        "target": str(target),
        "before": before_sha,
        "after": after_sha,
        "updated": before_sha != after_sha,
        "active": active,
        "output": output[-2000:],
    }
    if not payload["ok"]:
        payload["error"] = f"{service} не поднялся после обновления"
    return (0 if payload["ok"] else 5), payload


def action_zapret_install_easy(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    """Установить zapret из git-репозитория: clone + install_easy.sh.

    install_easy.sh интерактивен; мы подаём пустой stdin, чтобы он взял
    значения по умолчанию (nftables, hostlist и т.п.).
    """

    service = validate_service(args.service or "zapret2")
    url = validate_source_url(args.git_url or DEFAULT_ZAPRET2_GIT_URL)
    target = Path("/opt/zapret2" if service == "zapret2" else "/opt/zapret")
    installer = target / "install_easy.sh"

    if getattr(args, "update", False):
        return _update_zapret_sources(service, target, url, args)

    cloned = False
    if not installer.is_file():
        target.parent.mkdir(parents=True, exist_ok=True)
        clone = util.run(
            ["git", "clone", "--depth", "1", url, str(target)],
            check=False,
            timeout=args.timeout,
        )
        cloned = clone.returncode == 0
        if not cloned and not installer.is_file():
            detail = (clone.stderr or clone.stdout or "").strip()[-2000:]
            return 5, {"ok": False, "error": f"git clone не удался: {detail or clone.returncode}"}

    # Повторный запуск мастер-установщика не нужен и опасен: если служба уже
    # есть в systemd — считаем zapret установленным.
    unit = util.run(["systemctl", "cat", service], check=False, timeout=10)
    if unit.returncode == 0:
        return 0, {
            "ok": True,
            "service": service,
            "target": str(target),
            "cloned": cloned,
            "already_installed": True,
        }

    env = dict(os.environ)
    env["ZAPRET_TARGET"] = str(target)
    env["ZAPRET_TARGET_RW"] = str(target)
    env["ZAPRET_BASE"] = str(target)
    installed = util.run(
        ["sh", str(installer)],
        check=False,
        timeout=args.timeout,
        env=env,
        input="",
    )
    output = ((installed.stdout or "") + (installed.stderr or "")).strip()
    if installed.returncode != 0:
        return 5, {
            "ok": False,
            "error": f"install_easy.sh завершился с кодом {installed.returncode}",
            "output": output[-4000:],
        }
    return 0, {
        "ok": True,
        "service": service,
        "target": str(target),
        "cloned": cloned,
        "output": output[-4000:],
    }


def action_zapret_set_enabled(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    if not args.service:
        raise HelperError("zapret.set_enabled: требуется --service")
    if not args.enabled:
        raise HelperError("zapret.set_enabled: требуется --enabled on|off")
    service = validate_service(args.service)
    enabled = validate_enabled(args.enabled)

    command = ["systemctl", "enable" if enabled == "on" else "disable", service]
    completed = util.run(command, check=False, timeout=20)
    status_cp = util.run(["systemctl", "is-enabled", service], check=False, timeout=10)
    active = (status_cp.stdout or "").strip()
    payload = {
        "ok": completed.returncode == 0,
        "service": service,
        "enabled": active == "enabled",
        "state": active,
    }
    return (0 if payload["ok"] else 5), payload


def action_zapret_save_profile(
    args: argparse.Namespace,
) -> tuple[int, dict[str, object]]:
    """Сохранить наш конфиг zapret2 и hostlists перед обновлением."""

    from . import updates

    try:
        meta = updates.save_profile()
    except updates.UpdateError as exc:
        return 5, {"ok": False, "error": str(exc)}
    return 0, {"ok": True, **meta}


def action_zapret_restore_profile(
    args: argparse.Namespace,
) -> tuple[int, dict[str, object]]:
    """Вернуть наш конфиг zapret2 и hostlists после обновления."""

    from . import updates

    try:
        result = updates.restore_profile()
    except updates.UpdateError as exc:
        return 5, {"ok": False, "error": str(exc)}
    return 0, {"ok": True, **result}


def action_net_clear_ipv6_blackholes(
    args: argparse.Namespace,
) -> tuple[int, dict[str, object]]:
    """Убрать маршруты-заглушки IPv6, оставленные VPN-клиентом.

    Отказываемся работать, если VPN ещё активен: маршруты — часть его
    kill-switch, снимать их на ходу нельзя.
    """

    from . import doctor

    tunnel = doctor.vpn_interfaces()
    if tunnel:
        return 3, {
            "ok": False,
            "error": f"VPN ещё работает ({', '.join(tunnel)}) — убирать заглушки нельзя",
            "outstanding": doctor.ipv6_blackhole_routes(),
        }

    removed, left = doctor.clear_ipv6_blackholes()
    return (0 if not left else 4), {
        "ok": not left,
        "removed": removed,
        "outstanding": left,
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



def action_hostlist_import(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    """Добавить домены в пользовательский блок hostlist."""

    from . import hostlist as hostlist_mod

    if not args.hostlist:
        raise HelperError("hostlist.import: требуется --hostlist")
    target = validate_path(args.hostlist, must_exist=False)

    domains: list[str] = list(args.domain or [])
    if args.list_file:
        source = Path(args.list_file)
        if not source.is_file():
            raise HelperError(f"файл списка не найден: {source}")
        domains.extend(hostlist_mod.parse_list_text(source.read_text(encoding="utf-8", errors="replace")))
    if args.url:
        text = hostlist_mod.fetch_list(args.url, timeout=args.timeout)
        domains.extend(hostlist_mod.parse_list_text(text))
    if not domains:
        raise HelperError("hostlist.import: нет доменов (--domain / --list-file / --url)")

    section = getattr(args, "section", "user") or "user"
    result = hostlist_mod.add_domains(target, domains, target_section=section)
    restarted = False
    if result.added and args.restart_on_change and args.service:
        service = validate_service(args.service)
        completed = util.run(["systemctl", "restart", service], check=False, timeout=40)
        restarted = completed.returncode == 0

    return 0, {
        "ok": True,
        "hostlist": str(target),
        "added": result.added,
        "skipped": result.skipped,
        "total": result.total,
        "restarted": restarted,
    }


def action_hostlist_remove(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    """Удалить домены из блоков LinFix (чужие строки не трогаем)."""

    from . import hostlist as hostlist_mod

    if not args.hostlist:
        raise HelperError("hostlist.remove: требуется --hostlist")
    if not args.domain:
        raise HelperError("hostlist.remove: требуется хотя бы один --domain")
    target = validate_path(args.hostlist, must_exist=False)

    result = hostlist_mod.remove_domains(target, list(args.domain))
    restarted = False
    if result.added and args.restart_on_change and args.service:
        service = validate_service(args.service)
        completed = util.run(["systemctl", "restart", service], check=False, timeout=40)
        restarted = completed.returncode == 0

    return 0, {
        "ok": True,
        "hostlist": str(target),
        "removed": result.added,
        "total": result.total,
        "restarted": restarted,
    }


def action_hostlist_load_source(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    """Запустить штатный скрипт-источник zapret (get_antizapret и т.п.)."""

    from . import hostlist as hostlist_mod

    if not args.script:
        raise HelperError("hostlist.load_source: требуется --script")
    directory = Path(args.ipset_dir or "/opt/zapret2/ipset")
    if not directory.is_dir():
        raise HelperError(f"каталог ipset не найден: {directory}")
    return hostlist_mod.load_source(args.script, directory)



def action_sysctl_set(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    """Изменить один разрешённый sysctl (сейчас — только tcp_mtu_probing)."""

    if args.key not in ALLOWED_SYSCTLS:
        raise HelperError(f"sysctl.set: ключ не разрешён: {args.key}")
    if args.value not in {"0", "1", "2"}:
        raise HelperError("sysctl.set: значение должно быть 0, 1 или 2")

    completed = util.run(
        ["sysctl", "-w", f"{args.key}={args.value}"], check=False, timeout=30
    )
    path = Path(f"/proc/sys/{args.key.replace('.', '/')}")
    try:
        current = path.read_text().strip()
    except OSError:
        current = ""
    return (0 if current == args.value else 5), {
        "ok": current == args.value,
        "key": args.key,
        "value": current,
        "error": (completed.stderr or "").strip() if current != args.value else "",
    }


def action_mtu_set(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    """Задать MTU интерфейса (576..1500).

    Увеличение разрешено только с явным --allow-increase: по умолчанию
    интерфейс можно лишь сузить, чтобы случайно не заявить MTU больше,
    чем пропускает провайдер.
    """

    if not args.interface or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,32}", args.interface):
        raise HelperError("mtu.set: требуется корректное --interface")
    if args.mtu is None or not 576 <= args.mtu <= 1500:
        raise HelperError("mtu.set: --mtu должен быть в диапазоне 576..1500")

    current = util.run(["ip", "-o", "link", "show", args.interface], check=False, timeout=10)
    if current.returncode != 0:
        raise HelperError(f"интерфейс не найден: {args.interface}")
    match = re.search(r"\bmtu\s+(\d+)", current.stdout or "")
    if match and int(match.group(1)) < args.mtu and not getattr(args, "allow_increase", False):
        raise HelperError(
            f"mtu.set: {args.interface} уже имеет меньший MTU ({match.group(1)}); "
            "для увеличения нужен --allow-increase"
        )

    completed = util.run(
        ["ip", "link", "set", args.interface, "mtu", str(args.mtu)],
        check=False,
        timeout=30,
    )
    after = util.run(["ip", "-o", "link", "show", args.interface], check=False, timeout=10)
    now = re.search(r"\bmtu\s+(\d+)", after.stdout or "")
    value = int(now.group(1)) if now else None
    return (0 if value == args.mtu else 5), {
        "ok": value == args.mtu,
        "interface": args.interface,
        "mtu": value,
        "error": (completed.stderr or "").strip() if value != args.mtu else "",
    }



def action_zapret_neutralize_hostlist_args(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    """Обезвредить профили, где hostlist передан без ``--hostlist=``.

    Раньше такой профиль молча игнорировался. Просто добавить ``--hostlist=``
    нельзя: профиль включается и может ломать TLS (проверено на практике).
    Безопасный вариант — закомментировать строку целиком: поведение остаётся
    прежним, но в конфиге больше нет неоднозначности.
    """

    config_path = Path(args.config_path or "/opt/zapret2/config")
    if not config_path.is_file():
        raise HelperError(f"конфиг zapret не найден: {config_path}")

    original = config_path.read_text(encoding="utf-8", errors="replace")
    result_lines: list[str] = []
    commented: list[str] = []
    for line in original.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            result_lines.append(line)
            continue
        tokens = stripped.split()
        if "--hostlist=" in stripped or "--hostlist-exclude=" in stripped:
            result_lines.append(line)
            continue
        bare = [
            token
            for token in tokens
            if not token.startswith("-")
            and "=" not in token
            and (token.endswith(".txt") or token.endswith(".gz"))
        ]
        if bare:
            indent = line[: len(line) - len(line.lstrip())]
            result_lines.append(f"{indent}# linfix: профиль без --hostlist обезврежен")
            result_lines.append(f"{indent}# {stripped}")
            commented.append(stripped)
        else:
            result_lines.append(line)

    if not commented:
        return 0, {"ok": True, "changed": False, "path": str(config_path), "lines": []}

    backup = util.backup_file(config_path)
    util.atomic_write(config_path, "\n".join(result_lines) + "\n")
    return 0, {
        "ok": True,
        "changed": True,
        "path": str(config_path),
        "backup": str(backup) if backup else None,
        "lines": commented,
    }


def action_zapret_restore_config(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    """Вернуть конфиг zapret из бэкапа (автооткат после неудачной правки)."""

    if not args.backup:
        raise HelperError("zapret.restore_config: требуется --backup")
    backup = Path(args.backup)
    if not backup.is_file():
        raise HelperError(f"бэкап не найден: {backup}")
    target = Path(args.config_path or "/opt/zapret2/config")
    util.restore_file(backup, target)
    return 0, {"ok": True, "restored": str(target), "from": str(backup)}



def action_zapret_blockcheck_stop(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    """Прервать идущий blockcheck2 (подбор стратегий) и вернуть zapret."""

    from . import blockcheck

    service = validate_service(args.service or "zapret2")
    install_dir = Path(args.install_dir or "/opt/zapret2")
    if not (install_dir / "blockcheck2.sh").is_file():
        raise HelperError(f"blockcheck2.sh не найден в {install_dir}")

    # 1. Прибить процессы замеров (самая неприятная вызов — «убить скан»).
    stopped: list[int] = []
    for pid in blockcheck.stray_processes():
        if pid <= 0:
            continue
        try:
            os.kill(pid, signal.SIGTERM)
            stopped.append(pid)
        except (ProcessLookupError, PermissionError):
            pass
    if stopped:
        deadline = time.monotonic() + 8
        while time.monotonic() < deadline and blockcheck.stray_processes():
            time.sleep(0.3)
        for pid in blockcheck.stray_processes():
            try:
                os.kill(pid, signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        time.sleep(1.0)

    # 2. Служба могла всё ещё вернуться самой (helper-хозяин скана),
    #    старт повторно безопаснее всего — системный майдемов не создан.
    active = (
        util.run(["systemctl", "is-active", service], check=False, timeout=10).stdout or ""
    ).strip()
    if active != "active":
        util.run(["systemctl", "start", service], check=False, timeout=60)

    return 0, {"ok": True, "stopped": stopped}


def action_zapret_blockcheck(args: argparse.Namespace) -> tuple[int, dict[str, object]]:
    """Подобрать стратегии desync через blockcheck2 (root).

    Helper уже root, поэтому blockcheck2 не пытается сам звать sudo —
    в противном случае он падает с «a terminal is required to read the
    password». Служба zapret останавливается на время замеров и
    восстанавливается в любом случае.
    """

    from . import blockcheck

    service = validate_service(args.service or "zapret2")
    install_dir = Path(args.install_dir or "/opt/zapret2")
    if not (install_dir / "blockcheck2.sh").is_file():
        raise HelperError(f"blockcheck2.sh не найден в {install_dir}")
    level = args.level or "quick"
    if level not in blockcheck.SCAN_LEVELS:
        raise HelperError(f"недопустимый уровень сканирования: {level}")
    # Список приходит из [blockcheck] domains в конфиге LinFix. Здесь
    # остаётся только страховка на случай вызова helper'а напрямую.
    domains = list(args.domain or []) or ["rutracker.org", "telegram.org"]

    was_active = (
        util.run(["systemctl", "is-active", service], check=False, timeout=10).stdout or ""
    ).strip() == "active"

    # Маркер ставим ДО остановки службы, а снимаем после её возврата:
    # всё это время GUI/CLI обязаны считать, что идёт подбор, и не
    # поднимать zapret (иначе замеры испорчены).
    blockcheck.write_lock(
        service=service,
        level=level,
        domains=domains,
        install_dir=str(install_dir),
    )
    stopped = False
    try:
        if was_active:
            util.run(["systemctl", "stop", service], check=False, timeout=30)
            stopped = True

        try:
            result = blockcheck.run_blockcheck(
                install_dir,
                domains=domains,
                level=level,
                # Должно быть меньше внешнего таймаута вызывающего, иначе helper
                # убьют раньше, чем он успеет вернуть zapret обратно.
                timeout=min(max(args.timeout, 120.0), 2100.0),
                repeats=max(1, int(args.repeats or 2)),
                fast=getattr(args, "fast", "on") != "off",
            )
        finally:
            if stopped:
                util.run(["systemctl", "start", service], check=False, timeout=60)
    finally:
        blockcheck.clear_lock()

    # Результат возвращается вызывающему и живёт только в памяти его
    # процесса. Ничего не пишем на диск: старые ошибки подбора не должны
    # показываться в следующих запусках LinFix.
    best = result.best
    return 0, {
        "ok": True,
        "stopped": stopped,
        "domains": result.domains,
        "level": result.level,
        "works_without_bypass": result.works_without_bypass,
        "strategies": [
            {"success": item.success, "total": item.total, "args": item.args}
            for item in result.strategies[:10]
        ],
        "best": (
            {"success": best.success, "total": best.total, "args": best.args}
            if best
            else None
        ),
        "error": result.error,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="linfix-helper",
        description="Привилегированные действия LinFix (только белый список)",
    )
    parser.add_argument("action", choices=sorted(ALLOWED_ACTIONS))
    parser.add_argument("--hostlist", help="hostlist для domains.merge")
    parser.add_argument("--domain", action="append", default=[], help="домен (можно повторять)")
    parser.add_argument("--marker", default=DEFAULT_MARKER)
    parser.add_argument("--service", help="служба zapret (zapret | zapret2)")
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
    parser.add_argument("--git-url", default="", help="git-репозиторий zapret для zapret.install_easy")
    parser.add_argument(
        "--update",
        action="store_true",
        help="обновить существующую установку zapret (исходники и бинарники)",
    )
    parser.add_argument("--asset", default="", help="имя релизного архива zapret2")
    parser.add_argument("--asset-url", default="", help="прямая ссылка на архив")
    parser.add_argument("--zapret-version", default="", help="версия zapret2 для записи")
    parser.add_argument("--asset-sha256", default="", help="ожидаемая sha256 архива zapret2")
    parser.add_argument("--sha256", default="", help="ожидаемый SHA256")
    parser.add_argument("--version", default="", help="версия TgWsProxy")
    parser.add_argument("--dest", default=str(tgws.BINARY), help="куда установить бинарник")
    parser.add_argument("--timeout", type=float, default=600.0, help="таймаут скачивания")
    parser.add_argument("--config", default=str(DEFAULT_CONFIG_PATH), help="путь к config.toml")
    parser.add_argument("--enabled", choices=["on", "off"], help="для config.guard / zapret.set_enabled")
    parser.add_argument("--uuid", help="для profile.*")
    parser.add_argument("--name", help="для profile.set")
    parser.add_argument("--strategy", default=profiles.GENERAL, help="для profile.set")
    parser.add_argument("--list-file", default="", help="файл со списком доменов")
    parser.add_argument("--ipset-dir", default="/opt/zapret2/ipset", help="каталог ipset zapret")
    parser.add_argument("--script", default="", help="скрипт-источник zapret")
    parser.add_argument(
        "--section",
        choices=["cf", "user"],
        default="user",
        help="в какой блок hostlist добавлять домены",
    )
    parser.add_argument("--key", default="", help="sysctl-ключ")
    parser.add_argument("--value", default="", help="значение sysctl")
    parser.add_argument("--interface", default="", help="сетевой интерфейс для mtu.set")
    parser.add_argument("--mtu", type=int, default=None, help="MTU для mtu.set")
    parser.add_argument(
        "--allow-increase",
        action="store_true",
        help="разрешить mtu.set увеличивать MTU интерфейса",
    )
    parser.add_argument("--install-dir", default="", help="каталог zapret для blockcheck")
    parser.add_argument("--level", default="", help="уровень blockcheck: veryquick…force")
    parser.add_argument(
        "--fast",
        choices=["on", "off"],
        default="on",
        help="отсев заведомо неприменимых семейств (по умолчанию on)",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=2,
        help="повторов на тест; >1 включает параллельный режим blockcheck",
    )
    parser.add_argument("--config-path", default="/opt/zapret2/config", help="config zapret")
    parser.add_argument("--backup", default="", help="бэкап для zapret.restore_config")
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
        elif args.action == "hostlist.import":
            code, payload = action_hostlist_import(args)
        elif args.action == "hostlist.remove":
            code, payload = action_hostlist_remove(args)
        elif args.action == "hostlist.load_source":
            code, payload = action_hostlist_load_source(args)
        elif args.action == "sysctl.set":
            code, payload = action_sysctl_set(args)
        elif args.action == "mtu.set":
            code, payload = action_mtu_set(args)
        elif args.action == "zapret.neutralize_hostlist_args":
            code, payload = action_zapret_neutralize_hostlist_args(args)
        elif args.action == "zapret.restore_config":
            code, payload = action_zapret_restore_config(args)
        elif args.action == "tgws.install":
            code, payload = action_tgws_install(args)
        elif args.action == "config.guard":
            code, payload = action_config_guard(args)
        elif args.action == "config.blockcheck":
            code, payload = action_config_blockcheck(args)
        elif args.action == "net.clear_ipv6_blackholes":
            code, payload = action_net_clear_ipv6_blackholes(args)
        elif args.action == "zapret.save_profile":
            code, payload = action_zapret_save_profile(args)
        elif args.action == "zapret.restore_profile":
            code, payload = action_zapret_restore_profile(args)
        elif args.action == "zapret.set_enabled":
            code, payload = action_zapret_set_enabled(args)
        elif args.action == "zapret.install_easy":
            code, payload = action_zapret_install_easy(args)
        elif args.action == "zapret.stop":
            code, payload = action_zapret_stop(args)
        elif args.action == "zapret.blockcheck":
            code, payload = action_zapret_blockcheck(args)
        elif args.action == "zapret.blockcheck_stop":
            code, payload = action_zapret_blockcheck_stop(args)
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
