"""TgWsProxy: установка, конфиг, user-служба, ссылка.

Установка бинарника выполняется helper'ом (root). Скачивание идёт только по
HTTPS, SHA256 сверяется с digest со страницы релиза GitHub. Конфиг и user-unit
пишутся от имени пользователя.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shlex
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import github_web, util
from .cfdomains import is_valid_domain

UNIT_NAME = "tgwsproxy"
INSTALL_DIR = Path("/opt/tgwsproxy")
BINARY = INSTALL_DIR / "TgWsProxy"
REPO = "Flowseal/tg-ws-proxy"
MAX_BINARY_BYTES = 200 * 1024 * 1024
VERSION_RE = re.compile(r"^v[0-9A-Za-z._-]+$")

UNIT_TEMPLATE = """[Unit]
Description=TgWsProxy (Telegram WS proxy)
After=network-online.target graphical-session.target
Wants=network-online.target
PartOf=graphical-session.target
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
ExecStart={binary}
Restart=on-failure
RestartSec=3
# Бинарник TgWsProxy — tray-приложение: без X11/AppIndicator он падает,
# поэтому юнит привязан к графической сессии, а не к default.target.
Environment=DISPLAY={display}

[Install]
WantedBy=default.target
"""

#: CLI-режим: тот же прокси из исходников, без X11/трея.
CLI_UNIT_TEMPLATE = """[Unit]
Description=TgWsProxy (Telegram WS proxy, CLI)
After=network-online.target
Wants=network-online.target
StartLimitIntervalSec=60
StartLimitBurst=5

[Service]
Type=simple
WorkingDirectory={source}
Environment=PYTHONPATH={source}
ExecStart={python} -m proxy.tg_ws_proxy {args}
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
"""

MODE_TRAY = "tray"
MODE_CLI = "cli"
MODES = (MODE_TRAY, MODE_CLI)


def source_dir() -> Path:
    """Исходники TgWsProxy для CLI-режима — в каталоге пользователя.

    В /opt их писать нельзя без root: переключение режима не должно требовать
    пароля, иначе «Сохранить» в панели висит на запросе polkit.
    """

    return Path.home() / ".local" / "share" / "linfix" / "tgws-src"

ALLOWED_CONFIG_KEYS = {
    "host",
    "port",
    "cfproxy",
    "h2",
    "verbose",
    "pool_size",
    "log_max_mb",
    # Списки доменов: TgWsProxy превращает их в --cfproxy-domain /
    # --cfproxy-worker-domain при генерации user-юнита (см. cli_args).
    "cfproxy_user_domain",
    "cfproxy_worker_domain",
    # Включение worker-пути: без флага TgWsProxy игнорирует worker-домены.
    "cfproxy_worker_enabled",
    # linfix_mode остаётся только для чтения старых конфигов (см. detect_mode).
    "linfix_mode",
}


class TgwsError(util.NetfixError):
    """Ошибка TgWsProxy."""


@dataclass(frozen=True)
class Asset:
    name: str
    url: str
    digest: str
    size: int

    @property
    def sha256(self) -> str:
        if not self.digest.startswith("sha256:"):
            return ""
        return self.digest.split(":", 1)[1].lower()


@dataclass(frozen=True)
class Release:
    tag: str
    assets: tuple[Asset, ...]


def parse_release(raw: dict[str, object]) -> Release:
    tag = raw.get("tag_name")
    if not isinstance(tag, str) or not tag:
        raise TgwsError("в ответе GitHub нет tag_name")
    assets: list[Asset] = []
    raw_assets = raw.get("assets", [])
    if not isinstance(raw_assets, list):
        raise TgwsError("в ответе GitHub assets должен быть списком")
    for item in raw_assets:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        url = item.get("browser_download_url")
        digest = item.get("digest")
        size = item.get("size")
        if not isinstance(name, str) or not isinstance(url, str):
            continue
        assets.append(
            Asset(
                name=name,
                url=url,
                digest=digest if isinstance(digest, str) else "",
                size=size if isinstance(size, int) else 0,
            )
        )
    return Release(tag=tag, assets=tuple(assets))


def normalize_version(version: str | None) -> str | None:
    if not version:
        return None
    value = version.strip()
    if not value.startswith("v"):
        value = "v" + value
    if not VERSION_RE.match(value):
        raise TgwsError(f"некорректная версия: {version}")
    return value


def fetch_release(version: str | None = None, *, timeout: float = 20.0) -> Release:
    """Описание релиза: тег и файлы со страницы GitHub (без REST API).

    ``version=None`` — последний релиз. Для конкретной версии страница
    ``expanded_assets`` отдаёт тот же список файлов и их sha256.
    """

    tag = normalize_version(version)
    try:
        if tag is None:
            tag = github_web.latest_tag(REPO, timeout=timeout)
        web_assets = github_web.release_assets(REPO, tag, timeout=timeout)
    except github_web.GitHubWebError as exc:
        raise TgwsError(str(exc)) from exc

    assets = tuple(
        Asset(
            name=item.name,
            url=item.url,
            digest=f"sha256:{item.sha256}" if item.sha256 else "",
            size=item.size,
        )
        for item in web_assets
    )
    return Release(tag=tag, assets=assets)


def latest_version(*, timeout: float = 20.0) -> str | None:
    """Только тег последнего релиза — для проверки обновлений."""

    try:
        return github_web.latest_tag(REPO, timeout=timeout)
    except github_web.GitHubWebError as exc:
        raise TgwsError(str(exc)) from exc


def select_linux_asset(release: Release, arch: str = "amd64") -> Asset:
    wanted = f"TgWsProxy_linux_{arch}"
    for asset in release.assets:
        if asset.name == wanted:
            if len(asset.sha256) != 64:
                raise TgwsError(f"у {asset.name} нет корректного sha256 digest")
            return asset
    available = ", ".join(asset.name for asset in release.assets)
    raise TgwsError(f"в релизе {release.tag} нет {wanted} (есть: {available})")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verify_sha256(path: str | Path, expected: str) -> str:
    actual = sha256_file(path)
    expected = expected.strip().lower()
    if not expected or actual != expected:
        raise TgwsError(f"sha256 не совпадает: ожидался {expected or '—'}, получен {actual}")
    return actual


def download_to_temp(
    url: str,
    directory: str | Path,
    *,
    timeout: float = 30.0,
    max_bytes: int = MAX_BINARY_BYTES,
    attempts: int = 3,
) -> Path:
    """Скачать по HTTPS во временный файл в той же директории.

    ``timeout`` — таймаут простоя сокета (не всей загрузки): большие файлы
    качаются сколько нужно, но зависшее соединение обрывается. Неудачные
    попытки повторяются: GitHub release CDN периодически зависает на 302.
    """

    if not url.lower().startswith("https://"):
        raise TgwsError("скачивание разрешено только по HTTPS")
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)

    last_error: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        fd, tmp_name = tempfile.mkstemp(prefix=".TgWsProxy.", suffix=".download", dir=str(target_dir))
        tmp_path = Path(tmp_name)
        total = 0
        request = urllib.request.Request(url, headers={"User-Agent": "linfix/0.1"})
        try:
            with os.fdopen(fd, "wb") as out_handle:
                with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                    if not response.geturl().lower().startswith("https://"):
                        raise TgwsError("редирект на не-HTTPS запрещён")
                    while True:
                        chunk = response.read(1024 * 1024)
                        if not chunk:
                            break
                        total += len(chunk)
                        if total > max_bytes:
                            raise TgwsError(f"файл больше лимита {max_bytes} байт")
                        out_handle.write(chunk)
                out_handle.flush()
                os.fsync(out_handle.fileno())
        except TgwsError:
            try:
                tmp_path.unlink()
            except OSError:
                pass
            raise
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            last_error = exc
            try:
                tmp_path.unlink()
            except OSError:
                pass
            if attempt < attempts:
                time.sleep(min(2 * attempt, 5))
            continue
        except BaseException:
            try:
                tmp_path.unlink()
            except OSError:
                pass
            raise
        return tmp_path

    raise TgwsError(
        f"не удалось скачать {url} за {attempts} попыток: {last_error or 'нет соединения'}"
    )


def install_binary(source: str | Path, dest: str | Path, expected_sha256: str) -> dict[str, object]:
    actual = verify_sha256(source, expected_sha256)
    util.atomic_copy(source, dest, mode=0o755)
    target = Path(dest)
    return {
        "path": str(target),
        "sha256": actual,
        "size": target.stat().st_size,
    }


def install_from_url(
    url: str,
    dest: str | Path,
    expected_sha256: str,
    *,
    timeout: float = 30.0,
) -> dict[str, object]:
    target = Path(dest)
    temp = download_to_temp(url, target.parent, timeout=timeout)
    try:
        return install_binary(temp, target, expected_sha256)
    finally:
        try:
            temp.unlink()
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Конфиг
# ---------------------------------------------------------------------------


def is_installed(binary: str | Path = BINARY) -> bool:
    return Path(binary).exists()


def config_candidates(home: str | Path | None = None) -> tuple[Path, Path]:
    base = Path(home) if home is not None else Path.home()
    return (
        base / ".config" / "TgWsProxy" / "config.json",
        base / ".local" / "share" / "TgWsProxy" / "config.json",
    )


def find_config_path(home: str | Path | None = None) -> Path | None:
    for candidate in config_candidates(home):
        if candidate.exists():
            return candidate
    return None


def log_candidates(home: str | Path | None = None) -> tuple[Path, Path]:
    base = Path(home) if home is not None else Path.home()
    return (
        base / ".config" / "TgWsProxy" / "proxy.log",
        base / ".local" / "share" / "TgWsProxy" / "proxy.log",
    )


def find_log_path(home: str | Path | None = None) -> Path | None:
    for candidate in log_candidates(home):
        if candidate.exists():
            return candidate
    return None


def default_config(port: int = 1080, host: str = "127.0.0.1") -> dict[str, object]:
    return {
        "host": host,
        "port": port,
        "cfproxy": True,
        "cfproxy_user_domain": [],
        "cfproxy_worker_domain": [],
        "h2": True,
        "verbose": False,
        "log_max_mb": 5,
        "pool_size": 4,
        "secret": secrets.token_hex(16),
    }


def load_json_config(path: str | Path) -> dict[str, object]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise TgwsError(f"не удалось прочитать конфиг {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise TgwsError(f"конфиг {path} должен быть JSON-объектом")
    return data


def write_default_config(path: str | Path, port: int = 1080, host: str = "127.0.0.1") -> dict[str, object]:
    data = default_config(port, host)
    util.atomic_write(
        path,
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        mode=0o600,
    )
    return data


def ensure_config(
    *,
    create: bool = False,
    port: int = 1080,
    host: str = "127.0.0.1",
    path: str | Path | None = None,
    home: str | Path | None = None,
) -> tuple[Path | None, bool]:
    if path is not None:
        existing = Path(path)
        if existing.exists():
            return existing, False
        if create:
            write_default_config(existing, port, host)
            return existing, True
        return None, False

    found = find_config_path(home)
    if found is not None:
        return found, False
    if not create:
        return None, False

    target = config_candidates(home)[0]
    write_default_config(target, port, host)
    return target, True


def _validate_config_value(key: str, value: object) -> None:
    if key == "port":
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 65535:
            raise TgwsError(f"port: ожидается целое 1..65535, получено {value!r}")
    elif key == "pool_size":
        if not isinstance(value, int) or isinstance(value, bool) or not 1 <= value <= 64:
            raise TgwsError(f"pool_size: ожидается целое 1..64, получено {value!r}")
    elif key == "log_max_mb":
        if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0.5 <= value <= 100:
            raise TgwsError(f"log_max_mb: ожидается число 0.5..100, получено {value!r}")
    elif key == "linfix_mode":
        if value not in MODES:
            raise TgwsError(f"linfix_mode: ожидается {' | '.join(MODES)}, получено {value!r}")
    elif key == "host":
        if not isinstance(value, str) or not value.strip():
            raise TgwsError("host: ожидается непустая строка")
        if value not in {"127.0.0.1", "localhost", "::1"}:
            raise TgwsError("host: в MVP разрешён только loopback (127.0.0.1, localhost, ::1)")
    elif key in {"cfproxy", "h2", "verbose", "cfproxy_worker_enabled"}:
        if not isinstance(value, bool):
            raise TgwsError(f"{key}: ожидается bool, получено {type(value).__name__}")
    elif key in {"cfproxy_user_domain", "cfproxy_worker_domain"}:
        # Пустой список — это режим «домены с GitHub»: TgWsProxy видит
        # пустой cfproxy_user_domains и сам подтягивает список с GitHub.
        if not isinstance(value, list):
            raise TgwsError(f"{key}: ожидается список доменов")
        normalized: list[str] = []
        for item in value:
            if not isinstance(item, str) or not is_valid_domain(item.strip().lower()):
                raise TgwsError(f"{key}: некорректный домен {item!r}")
            domain = item.strip().lower()
            if domain not in normalized:
                normalized.append(domain)
        # Возвращаем нормализованный список: cli_args добавит из него флаги.
        return normalized


def update_config(
    path: str | Path,
    changes: dict[str, object],
) -> tuple[dict[str, object], Path | None, bool]:
    target = Path(path)
    data = load_json_config(target)
    before = json.dumps(data, sort_keys=True)
    for key, value in changes.items():
        if key not in ALLOWED_CONFIG_KEYS:
            raise TgwsError(f"ключ {key} нельзя менять через linfix")
        normalized = _validate_config_value(key, value)
        data[key] = value if normalized is None else normalized
    after = json.dumps(data, sort_keys=True)
    if before == after:
        return data, None, False
    backup = util.backup_file(target)
    util.atomic_write(
        target,
        json.dumps(data, ensure_ascii=False, indent=2) + "\n",
        mode=0o600,
    )
    return data, backup, True


# ---------------------------------------------------------------------------
# Ротация CF-доменов: сохранение пользовательского списка
# ---------------------------------------------------------------------------


def rotation_file(home: str | Path | None = None) -> Path:
    """Файл с пользовательскими доменами для восстановления ротации."""

    base = Path(home) if home is not None else Path.home()
    return base / ".config" / "linfix" / "tgws-rotation.json"


def load_rotation_domains(home: str | Path | None = None) -> list[str]:
    """Прочитать сохранённые пользовательские домены ротации."""

    path = rotation_file(home)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError):
        return []
    value = data.get("domains") if isinstance(data, dict) else None
    try:
        return _validate_config_value("cfproxy_user_domain", value) or []
    except TgwsError:
        return []


def save_rotation_domains(domains: list[str], home: str | Path | None = None) -> Path:
    """Сохранить пользовательские домены (для переключения туда и обратно)."""

    normalized = _validate_config_value("cfproxy_user_domain", domains) or []
    if not normalized:
        raise TgwsError("список доменов ротации не может быть пустым")
    path = rotation_file(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    util.atomic_write(
        path,
        json.dumps({"domains": normalized}, ensure_ascii=False, indent=2) + "\n",
        mode=0o600,
    )
    return path


def build_link(secret: str, host: str = "127.0.0.1", port: int = 1080) -> str:
    return f"tg://proxy?server={host}&port={port}&secret=dd{secret}"


def read_link(
    host: str = "127.0.0.1",
    port: int = 1080,
    *,
    home: str | Path | None = None,
) -> str | None:
    path = find_config_path(home)
    if path is None:
        return None
    data = load_json_config(path)
    secret = data.get("secret")
    if not isinstance(secret, str) or not secret:
        return None
    config_host = data.get("host") if isinstance(data.get("host"), str) else host
    config_port = data.get("port") if isinstance(data.get("port"), int) else port
    return build_link(secret, config_host, config_port)


# ---------------------------------------------------------------------------
# user-служба
# ---------------------------------------------------------------------------


def _detect_display() -> str:
    """DISPLAY текущей сессии; при запуске из CLI — из user-manager'а."""

    display = os.environ.get("DISPLAY", "").strip()
    if display:
        return display
    completed = util.run(["systemctl", "--user", "show-environment"], check=False, timeout=5)
    for line in (completed.stdout or "").splitlines():
        if line.startswith("DISPLAY="):
            value = line.split("=", 1)[1].strip()
            if value:
                return value
    return ":0"


def display_available() -> bool:
    """Есть ли доступный X11-дисплей (нужен tray-режиму бинарника)."""

    display = _detect_display()
    if not display:
        return False
    number = display.rsplit(":", 1)[-1].split(".")[0]
    return Path(f"/tmp/.X11-unix/X{number}").exists()


def mode_file() -> Path:
    """Файл выбора режима LinFix — вне конфига TgWsProxy.

    TgWsProxy при старте перезаписывает свой ``config.json`` и выбрасывает
    незнакомые ключи, поэтому ``linfix_mode`` там не живёт.
    """

    return Path.home() / ".config" / "linfix" / "tgws-mode"


def detect_mode(config_path: str | Path | None = None) -> str:
    """Выбранный режим: файл LinFix → старый ключ в config.json → авто."""

    try:
        stored = mode_file().read_text(encoding="utf-8").strip()
    except OSError:
        stored = ""
    if stored in MODES:
        return stored

    # Совместимость с версиями, которые писали ключ в config.json.
    if config_path is not None and Path(config_path).exists():
        data = load_json_config(config_path)
        mode = data.get("linfix_mode")
        if mode in MODES:
            return str(mode)

    return MODE_TRAY if display_available() else MODE_CLI


def save_mode(mode: str) -> Path:
    if mode not in MODES:
        raise TgwsError(f"неизвестный режим: {mode}")
    target = mode_file()
    util.atomic_write(target, mode + "\n", mode=0o644)
    return target


def effective_mode(config_path: str | Path | None = None) -> str:
    """Фактический режим: сохранённое предпочтение, но tray невозможен без X11."""

    mode = detect_mode(config_path)
    if mode == MODE_TRAY and not display_available():
        return MODE_CLI
    return mode


def cli_args(data: dict[str, object]) -> list[str]:
    """Аргументы CLI-режима прокси из того же config.json."""

    args = [
        "--port",
        str(data.get("port", 1080)),
        "--host",
        str(data.get("host", "127.0.0.1")),
        "--log-file",
        str(log_candidates()[0]),
        "--log-max-mb",
        str(data.get("log_max_mb", 5)),
        "--pool-size",
        str(data.get("pool_size", 4)),
    ]
    secret = data.get("secret")
    if isinstance(secret, str) and secret:
        args += ["--secret", secret]
    if data.get("verbose"):
        args.append("--verbose")
    if data.get("cfproxy") is False:
        args.append("--no-cfproxy")
    if data.get("h2") is False:
        args.append("--no-h2")
    for key in ("cfproxy_user_domain", "cfproxy_worker_domain"):
        values = data.get(key)
        if isinstance(values, list):
            flag = "--cfproxy-domain" if key == "cfproxy_user_domain" else "--cfproxy-worker-domain"
            for value in values:
                if isinstance(value, str) and value:
                    args += [flag, value]
    return args


def render_unit(
    binary: str | Path,
    display: str | None = None,
    *,
    mode: str = MODE_TRAY,
    data: dict[str, object] | None = None,
    source: str | Path | None = None,
    python: str | None = None,
) -> str:
    if mode == MODE_CLI:
        args = " ".join(shlex.quote(part) for part in cli_args(data or {}))
        return CLI_UNIT_TEMPLATE.format(
            source=str(source or source_dir()),
            python=python or sys.executable,
            args=args,
        )
    return UNIT_TEMPLATE.format(binary=str(binary), display=display or _detect_display())


def user_unit_path(home: str | Path | None = None) -> Path:
    base = Path(home) if home is not None else Path.home()
    return base / ".config" / "systemd" / "user" / f"{UNIT_NAME}.service"


def install_user_unit(
    binary: str | Path = BINARY,
    *,
    home: str | Path | None = None,
    systemctl: bool = True,
    mode: str = MODE_TRAY,
    data: dict[str, object] | None = None,
    source: str | Path | None = None,
) -> tuple[Path, bool]:
    target = user_unit_path(home)
    text = render_unit(binary, mode=mode, data=data, source=source)
    if target.exists() and target.read_text(encoding="utf-8", errors="replace") == text:
        return target, False
    if target.exists():
        util.backup_file(target)
    util.atomic_write(target, text, mode=0o644)
    if systemctl:
        util.run(["systemctl", "--user", "daemon-reload"], check=False, timeout=20)
    return target, True


def enable_user_unit(*, start: bool = True, restart: bool = False) -> dict[str, object]:
    """Включить и запустить user-службу.

    ``systemctl start`` на уже работающей службе ничего не делает: процесс
    продолжает работать со старым бинарником. Поэтому при ``restart=True``
    используем ``systemctl restart`` — иначе после обновления версия в
    state.json новая, а в памяти старая.
    """

    enabled = util.run(["systemctl", "--user", "enable", UNIT_NAME], check=False, timeout=20)
    started_code: int | None = None
    started_out = ""
    action = "start"
    if start:
        if restart:
            action = "restart"
        started = util.run(
            ["systemctl", "--user", action, UNIT_NAME], check=False, timeout=40
        )
        started_code = started.returncode
        started_out = (started.stderr or started.stdout or "").strip()
    return {
        "enabled_ok": enabled.returncode == 0,
        "enabled_error": (enabled.stderr or "").strip(),
        "started": start,
        "started_action": action,
        "started_ok": started_code == 0 if started_code is not None else None,
        "started_error": started_out,
    }


def service_state() -> str:
    completed = util.run(["systemctl", "--user", "is-active", UNIT_NAME], check=False, timeout=10)
    return (completed.stdout or "").strip() or "inactive"


def running_mode() -> str | None:
    """Фактический режим запущенного процесса — независимо от config.json."""

    completed = util.run(["pgrep", "-af", UNIT_NAME], check=False, timeout=10)
    lines = [line for line in (completed.stdout or "").splitlines() if line.strip()]
    if not lines:
        completed = util.run(["pgrep", "-af", "tg_ws_proxy"], check=False, timeout=10)
        lines = [line for line in (completed.stdout or "").splitlines() if line.strip()]
    if not lines:
        return None
    for line in lines:
        if "proxy.tg_ws_proxy" in line or "tg_ws_proxy" in line:
            return MODE_CLI
    return MODE_TRAY
