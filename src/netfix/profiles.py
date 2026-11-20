"""Профили сетей NetworkManager (M4): структура файлов и матчинг UUID."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:  # Python 3.11+
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - python 3.10
    import tomli as tomllib  # type: ignore[no-redef]

GENERAL = "general"


@dataclass(frozen=True)
class Profile:
    name: str
    strategy: str = GENERAL


def load_profiles(path: str | Path) -> dict[str, Profile]:
    target = Path(path)
    if not target.exists():
        return {}
    raw = tomllib.loads(target.read_text(encoding="utf-8"))
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
