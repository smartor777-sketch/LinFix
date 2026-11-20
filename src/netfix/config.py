"""Чтение и валидация конфигурации NetFix (TOML)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

DEFAULT_CONFIG_PATH = Path("/etc/netfix/config.toml")
STATE_PATH = Path("/var/lib/netfix/state.json")
DEFAULT_CF_DOMAINS_URL = (
    "https://raw.githubusercontent.com/Flowseal/tg-ws-proxy/main/.github/cfproxy-domains.txt"
)
DEFAULT_MARKER = "# netfix: tgws cf domains"


class ConfigError(Exception):
    """Некорректный конфиг."""


@dataclass(frozen=True)
class GeneralConfig:
    log_level: str = "info"
    language: str = "ru"


@dataclass(frozen=True)
class ZapretConfig:
    install_dir: str = "/opt/zapret"
    service: str = "zapret"
    hostlist: str = ""
    restart_on_change: bool = True


@dataclass(frozen=True)
class TgwsConfig:
    install_dir: str = "/opt/tgwsproxy"
    binary: str = "/opt/tgwsproxy/TgWsProxy"
    port: int = 1080
    host: str = "127.0.0.1"
    autostart: bool = True


@dataclass(frozen=True)
class ListsConfig:
    cf_domains_url: str = DEFAULT_CF_DOMAINS_URL
    update_days: int = 7
    marker: str = DEFAULT_MARKER


@dataclass(frozen=True)
class GuardConfig:
    enabled: bool = True
    window_seconds: int = 60
    error_threshold: int = 20


@dataclass(frozen=True)
class ProfilesConfig:
    enabled: bool = False


@dataclass(frozen=True)
class Config:
    general: GeneralConfig = field(default_factory=GeneralConfig)
    zapret: ZapretConfig = field(default_factory=ZapretConfig)
    tgws: TgwsConfig = field(default_factory=TgwsConfig)
    lists: ListsConfig = field(default_factory=ListsConfig)
    guard: GuardConfig = field(default_factory=GuardConfig)
    profiles: ProfilesConfig = field(default_factory=ProfilesConfig)
    source: Path | None = None
    warnings: tuple[str, ...] = ()


def _expect_section(raw: dict[str, Any], name: str) -> dict[str, Any]:
    value = raw.get(name, {})
    if not isinstance(value, dict):
        raise ConfigError(f"секция [{name}] должна быть таблицей")
    return value


def _get_typed(
    section: dict[str, Any],
    section_name: str,
    key: str,
    default: Any,
    warnings: list[str],
) -> Any:
    if key not in section:
        return default
    value = section[key]
    expected = type(default)
    if expected is bool:
        valid = isinstance(value, bool)
    elif expected is int:
        valid = isinstance(value, int) and not isinstance(value, bool)
    elif expected is str:
        valid = isinstance(value, str)
    else:  # pragma: no cover - на будущее
        valid = True
    if not valid:
        raise ConfigError(
            f"[{section_name}] {key}: ожидался {expected.__name__}, получено {type(value).__name__}"
        )
    return value


def _warn_unknown(section: dict[str, Any], section_name: str, known: set[str], warnings: list[str]) -> None:
    for key in sorted(set(section) - known):
        warnings.append(f"неизвестный ключ [{section_name}] {key} — игнорируется")


def load_config(path: str | Path | None = None) -> Config:
    """Загрузить конфиг или вернуть значения по умолчанию.

    Явный аргумент важнее переменной окружения ``NETFIX_CONFIG``.
    Отсутствие файла — не ошибка: используются дефолты из SPEC.
    """

    if path is None:
        env_path = os.environ.get("NETFIX_CONFIG")
        path = Path(env_path) if env_path else DEFAULT_CONFIG_PATH
    config_path = Path(path)

    warnings: list[str] = []
    if not config_path.exists():
        warnings.append(f"конфиг {config_path} не найден, используются значения по умолчанию")
        return Config(source=None, warnings=tuple(warnings))

    try:
        raw = tomllib.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"не удалось прочитать {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"{config_path}: корень TOML должен быть таблицей")

    general_raw = _expect_section(raw, "general")
    zapret_raw = _expect_section(raw, "zapret")
    tgws_raw = _expect_section(raw, "tgws")
    lists_raw = _expect_section(raw, "lists")
    guard_raw = _expect_section(raw, "guard")
    profiles_raw = _expect_section(raw, "profiles")

    _warn_unknown(general_raw, "general", {"log_level", "language"}, warnings)
    _warn_unknown(
        zapret_raw,
        "zapret",
        {"install_dir", "service", "hostlist", "restart_on_change"},
        warnings,
    )
    _warn_unknown(
        tgws_raw,
        "tgws",
        {"install_dir", "binary", "port", "host", "autostart"},
        warnings,
    )
    _warn_unknown(lists_raw, "lists", {"cf_domains_url", "update_days", "marker"}, warnings)
    _warn_unknown(guard_raw, "guard", {"enabled", "window_seconds", "error_threshold"}, warnings)
    _warn_unknown(profiles_raw, "profiles", {"enabled"}, warnings)
    for key in sorted(set(raw) - {"general", "zapret", "tgws", "lists", "guard", "profiles"}):
        warnings.append(f"неизвестная секция [{key}] — игнорируется")

    general_defaults = GeneralConfig()
    zapret_defaults = ZapretConfig()
    tgws_defaults = TgwsConfig()
    lists_defaults = ListsConfig()
    guard_defaults = GuardConfig()
    profiles_defaults = ProfilesConfig()

    general = GeneralConfig(
        log_level=_get_typed(general_raw, "general", "log_level", general_defaults.log_level, warnings),
        language=_get_typed(general_raw, "general", "language", general_defaults.language, warnings),
    )
    zapret = ZapretConfig(
        install_dir=_get_typed(
            zapret_raw, "zapret", "install_dir", zapret_defaults.install_dir, warnings
        ),
        service=_get_typed(zapret_raw, "zapret", "service", zapret_defaults.service, warnings),
        hostlist=_get_typed(zapret_raw, "zapret", "hostlist", zapret_defaults.hostlist, warnings),
        restart_on_change=_get_typed(
            zapret_raw, "zapret", "restart_on_change", zapret_defaults.restart_on_change, warnings
        ),
    )
    tgws = TgwsConfig(
        install_dir=_get_typed(tgws_raw, "tgws", "install_dir", tgws_defaults.install_dir, warnings),
        binary=_get_typed(tgws_raw, "tgws", "binary", tgws_defaults.binary, warnings),
        port=_get_typed(tgws_raw, "tgws", "port", tgws_defaults.port, warnings),
        host=_get_typed(tgws_raw, "tgws", "host", tgws_defaults.host, warnings),
        autostart=_get_typed(tgws_raw, "tgws", "autostart", tgws_defaults.autostart, warnings),
    )
    lists = ListsConfig(
        cf_domains_url=_get_typed(
            lists_raw, "lists", "cf_domains_url", lists_defaults.cf_domains_url, warnings
        ),
        update_days=_get_typed(lists_raw, "lists", "update_days", lists_defaults.update_days, warnings),
        marker=_get_typed(lists_raw, "lists", "marker", lists_defaults.marker, warnings),
    )
    guard = GuardConfig(
        enabled=_get_typed(guard_raw, "guard", "enabled", guard_defaults.enabled, warnings),
        window_seconds=_get_typed(
            guard_raw, "guard", "window_seconds", guard_defaults.window_seconds, warnings
        ),
        error_threshold=_get_typed(
            guard_raw, "guard", "error_threshold", guard_defaults.error_threshold, warnings
        ),
    )
    profiles = ProfilesConfig(
        enabled=_get_typed(profiles_raw, "profiles", "enabled", profiles_defaults.enabled, warnings),
    )

    if tgws.port < 1 or tgws.port > 65535:
        raise ConfigError(f"[tgws] port: недопустимый порт {tgws.port}")
    if general.log_level not in {"debug", "info", "warn", "error"}:
        raise ConfigError(f"[general] log_level: недопустимое значение {general.log_level!r}")
    if general.language not in {"ru", "en"}:
        raise ConfigError(f"[general] language: недопустимое значение {general.language!r}")
    if guard.window_seconds <= 0:
        raise ConfigError("[guard] window_seconds должен быть больше 0")
    if guard.error_threshold <= 0:
        raise ConfigError("[guard] error_threshold должен быть больше 0")

    return Config(
        general=general,
        zapret=zapret,
        tgws=tgws,
        lists=lists,
        guard=guard,
        profiles=profiles,
        source=config_path,
        warnings=tuple(warnings),
    )
