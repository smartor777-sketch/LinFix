"""TgWsProxy: установка, конфиг, user-служба, ссылка (M2).

В M0 реализованы только безопасные операции чтения: поиск установки/конфига,
определение состояния user-службы и построение ``tg://``-ссылки.
"""

from __future__ import annotations

import json
from pathlib import Path

from . import util

UNIT_NAME = "tgwsproxy"
INSTALL_DIR = Path("/opt/tgwsproxy")
BINARY = INSTALL_DIR / "TgWsProxy"

CONFIG_CANDIDATES: tuple[Path, ...] = (
    Path.home() / ".config" / "TgWsProxy" / "config.json",
    Path.home() / ".local" / "share" / "TgWsProxy" / "config.json",
)
LOG_CANDIDATES: tuple[Path, ...] = (
    Path.home() / ".config" / "TgWsProxy" / "proxy.log",
    Path.home() / ".local" / "share" / "TgWsProxy" / "proxy.log",
)


def is_installed(binary: str | Path = BINARY) -> bool:
    return Path(binary).exists()


def find_config_path() -> Path | None:
    for candidate in CONFIG_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def find_log_path() -> Path | None:
    for candidate in LOG_CANDIDATES:
        if candidate.exists():
            return candidate
    return None


def service_state() -> str:
    completed = util.run(["systemctl", "--user", "is-active", UNIT_NAME], check=False, timeout=10)
    return (completed.stdout or "").strip() or "inactive"


def build_link(secret: str, host: str = "127.0.0.1", port: int = 1080) -> str:
    return f"tg://proxy?server={host}&port={port}&secret=dd{secret}"


def load_json_config(path: str | Path) -> dict[str, object]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise util.NetfixError(f"не удалось прочитать конфиг {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise util.NetfixError(f"конфиг {path} должен быть JSON-объектом")
    return data


def read_link(host: str = "127.0.0.1", port: int = 1080) -> str | None:
    path = find_config_path()
    if path is None:
        return None
    data = load_json_config(path)
    secret = data.get("secret")
    if not isinstance(secret, str) or not secret:
        return None
    config_host = data.get("host") if isinstance(data.get("host"), str) else host
    config_port = data.get("port") if isinstance(data.get("port"), int) else port
    return build_link(secret, config_host, config_port)


def install(*_args: object, **_kwargs: object) -> None:
    raise NotImplementedError("установка TgWsProxy будет реализована в M2")


def control(*_args: object, **_kwargs: object) -> None:
    raise NotImplementedError("управление службой TgWsProxy будет реализовано в M2")
