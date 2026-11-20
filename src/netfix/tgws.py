"""TgWsProxy: установка, конфиг, user-служба, ссылка.

Установка бинарника выполняется helper'ом (root). Скачивание идёт только по
HTTPS, SHA256 сверяется с ``asset.digest`` из GitHub API. Конфиг и user-unit
пишутся от имени пользователя.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import util

UNIT_NAME = "tgwsproxy"
INSTALL_DIR = Path("/opt/tgwsproxy")
BINARY = INSTALL_DIR / "TgWsProxy"
REPO = "Flowseal/tg-ws-proxy"
API_LATEST = f"https://api.github.com/repos/{REPO}/releases/latest"
API_TAG = f"https://api.github.com/repos/{REPO}/releases/tags/{{tag}}"
MAX_BINARY_BYTES = 200 * 1024 * 1024
VERSION_RE = re.compile(r"^v[0-9A-Za-z._-]+$")

UNIT_TEMPLATE = """[Unit]
Description=TgWsProxy (Telegram WS proxy)
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart={binary}
Restart=on-failure
RestartSec=3
# если приложение пишет в stdout — journald подхватит автоматически

[Install]
WantedBy=default.target
"""

ALLOWED_CONFIG_KEYS = {"host", "port", "cfproxy", "h2", "verbose", "pool_size"}


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


def fetch_https_text(url: str, *, timeout: float = 20.0) -> str:
    if not url.lower().startswith("https://"):
        raise TgwsError("разрешён только HTTPS")
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "netfix-linux/0.1",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            payload = response.read()
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise TgwsError(f"не удалось получить {url}: {exc}") from exc
    return payload.decode("utf-8", "replace")


def fetch_release(version: str | None = None, *, timeout: float = 20.0) -> Release:
    tag = normalize_version(version)
    url = API_TAG.format(tag=tag) if tag else API_LATEST
    raw = json.loads(fetch_https_text(url, timeout=timeout))
    if not isinstance(raw, dict):
        raise TgwsError("некорректный ответ GitHub API")
    return parse_release(raw)


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
    timeout: float = 600.0,
    max_bytes: int = MAX_BINARY_BYTES,
) -> Path:
    """Скачать по HTTPS во временный файл в той же директории."""

    if not url.lower().startswith("https://"):
        raise TgwsError("скачивание разрешено только по HTTPS")
    target_dir = Path(directory)
    target_dir.mkdir(parents=True, exist_ok=True)

    fd, tmp_name = tempfile.mkstemp(prefix=".TgWsProxy.", suffix=".download", dir=str(target_dir))
    tmp_path = Path(tmp_name)
    total = 0
    request = urllib.request.Request(url, headers={"User-Agent": "netfix-linux/0.1"})
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
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise
    return tmp_path


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
    timeout: float = 600.0,
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
    elif key == "host":
        if not isinstance(value, str) or not value.strip():
            raise TgwsError("host: ожидается непустая строка")
        if value not in {"127.0.0.1", "localhost", "::1"}:
            raise TgwsError("host: в MVP разрешён только loopback (127.0.0.1, localhost, ::1)")
    elif key in {"cfproxy", "h2", "verbose"}:
        if not isinstance(value, bool):
            raise TgwsError(f"{key}: ожидается bool, получено {type(value).__name__}")
    else:
        raise TgwsError(f"ключ {key} нельзя менять через netfix")


def update_config(
    path: str | Path,
    changes: dict[str, object],
) -> tuple[dict[str, object], Path | None, bool]:
    target = Path(path)
    data = load_json_config(target)
    before = json.dumps(data, sort_keys=True)
    for key, value in changes.items():
        if key not in ALLOWED_CONFIG_KEYS:
            raise TgwsError(f"ключ {key} нельзя менять через netfix")
        _validate_config_value(key, value)
        data[key] = value
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


def render_unit(binary: str | Path) -> str:
    return UNIT_TEMPLATE.format(binary=str(binary))


def user_unit_path(home: str | Path | None = None) -> Path:
    base = Path(home) if home is not None else Path.home()
    return base / ".config" / "systemd" / "user" / f"{UNIT_NAME}.service"


def install_user_unit(
    binary: str | Path = BINARY,
    *,
    home: str | Path | None = None,
    systemctl: bool = True,
) -> tuple[Path, bool]:
    target = user_unit_path(home)
    text = render_unit(binary)
    if target.exists() and target.read_text(encoding="utf-8", errors="replace") == text:
        return target, False
    if target.exists():
        util.backup_file(target)
    util.atomic_write(target, text, mode=0o644)
    if systemctl:
        util.run(["systemctl", "--user", "daemon-reload"], check=False, timeout=20)
    return target, True


def enable_user_unit(*, start: bool = True) -> dict[str, object]:
    enabled = util.run(["systemctl", "--user", "enable", UNIT_NAME], check=False, timeout=20)
    started_code: int | None = None
    started_out = ""
    if start:
        started = util.run(["systemctl", "--user", "start", UNIT_NAME], check=False, timeout=30)
        started_code = started.returncode
        started_out = (started.stderr or started.stdout or "").strip()
    return {
        "enabled_ok": enabled.returncode == 0,
        "enabled_error": (enabled.stderr or "").strip(),
        "started": start,
        "started_ok": started_code == 0 if started_code is not None else None,
        "started_error": started_out,
    }


def service_state() -> str:
    completed = util.run(["systemctl", "--user", "is-active", UNIT_NAME], check=False, timeout=10)
    return (completed.stdout or "").strip() or "inactive"
