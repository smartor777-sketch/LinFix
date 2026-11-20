"""Профили сетей NetworkManager (M4): структура файлов и матчинг UUID."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import util

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

GENERAL = "general"
PROFILES_PATH = Path("/etc/linfix/profiles.toml")
STRATEGIES = {"general"}
UUID_RE = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


@dataclass(frozen=True)
class Profile:
    name: str
    strategy: str = GENERAL


@dataclass(frozen=True)
class ActiveConnection:
    uuid: str
    name: str
    ctype: str


def validate_uuid(raw: str) -> str:
    value = raw.strip()
    if not UUID_RE.match(value):
        raise ValueError(f"некорректный UUID: {raw}")
    return value


def validate_name(raw: str) -> str:
    name = raw.strip()
    if not name or len(name) > 128:
        raise ValueError("name должно быть 1..128 символов")
    return name


def validate_strategy(raw: str) -> str:
    strategy = raw.strip()
    if strategy not in STRATEGIES:
        raise ValueError(f"неизвестная стратегия: {raw}")
    return strategy


def load_profiles(path: str | Path) -> dict[str, Profile]:
    target = Path(path)
    if not target.exists():
        return {}
    try:
        raw = tomllib.loads(target.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ValueError(f"не удалось прочитать {target}: {exc}") from exc
    profiles_raw: Any = raw.get("profiles", {})
    if not isinstance(profiles_raw, dict):
        raise ValueError("секция [profiles] должна быть таблицей")

    result: dict[str, Profile] = {}
    for uuid, value in profiles_raw.items():
        if not isinstance(value, dict):
            raise ValueError(f"профиль {uuid} должен быть таблицей")
        name = value.get("name", uuid)
        strategy = value.get("strategy", GENERAL)
        if not isinstance(name, str) or not isinstance(strategy, str):
            raise ValueError(f"профиль {uuid}: name/strategy должны быть строками")
        result[str(uuid)] = Profile(name=name, strategy=strategy)
    return result


def resolve_profile(profiles: dict[str, Profile], uuid: str | None) -> Profile:
    """Профиль по UUID; отсутствующий профиль — general (SPEC 13.2)."""

    if uuid and uuid in profiles:
        return profiles[uuid]
    return Profile(name=GENERAL, strategy=GENERAL)


def parse_nmcli_output(output: str) -> list[ActiveConnection]:
    """Разобрать ``nmcli -t -f UUID,NAME,TYPE connection show --active``."""

    connections: list[ActiveConnection] = []
    for line in output.splitlines():
        line = line.strip()
        if not line:
            continue
        parts = line.split(":", 2)
        if len(parts) < 2 or not parts[0]:
            continue
        name = parts[1] if len(parts) > 1 else ""
        ctype = parts[2] if len(parts) > 2 else ""
        connections.append(ActiveConnection(uuid=parts[0], name=name, ctype=ctype))
    return connections


def current_uuid(nmcli: str = "nmcli") -> str | None:
    if util.which(nmcli) is None:
        return None
    completed = util.run(
        [nmcli, "-t", "-f", "UUID,NAME,TYPE", "connection", "show", "--active"],
        check=False,
        timeout=10,
    )
    if completed.returncode != 0:
        return None
    connections = parse_nmcli_output(completed.stdout)
    return connections[0].uuid if connections else None


def save_profiles(path: str | Path, profiles: dict[str, Profile]) -> Path:
    target = Path(path)
    lines = ["[profiles]"]
    for uuid in sorted(profiles):
        profile = profiles[uuid]
        name = profile.name.replace('"', '\\"')
        lines.append(f'[profiles."{uuid}"]')
        lines.append(f'name = "{name}"')
        lines.append(f'strategy = "{profile.strategy}"')
        lines.append("")

    text = "\n".join(lines).strip() + "\n" if lines else ""
    backup = util.backup_file(target) if target.exists() else None
    util.atomic_write(target, text, mode=0o644)
    return target if backup is None else backup


def set_profile(
    uuid: str,
    name: str,
    strategy: str = GENERAL,
    *,
    path: str | Path = PROFILES_PATH,
) -> dict[str, object]:
    """Добавить/обновить профиль. Возвращает {"profiles": {...}, "changed": bool}."""

    normalized_uuid = validate_uuid(uuid)
    normalized_name = validate_name(name)
    normalized_strategy = validate_strategy(strategy)

    profiles = load_profiles(path)
    previous = profiles.get(normalized_uuid)
    new_profile = Profile(name=normalized_name, strategy=normalized_strategy)
    changed = previous != new_profile
    profiles[normalized_uuid] = new_profile
    if changed:
        save_profiles(path, profiles)
    return {"profiles": profiles, "changed": changed, "uuid": normalized_uuid}


def clear_profile(
    uuid: str,
    *,
    path: str | Path = PROFILES_PATH,
) -> dict[str, object]:
    """Удалить профиль для UUID."""

    normalized_uuid = validate_uuid(uuid)
    profiles = load_profiles(path)
    changed = normalized_uuid in profiles
    if changed:
        del profiles[normalized_uuid]
        save_profiles(path, profiles)
    return {"profiles": profiles, "changed": changed, "uuid": normalized_uuid}
