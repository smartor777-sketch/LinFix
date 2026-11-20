"""Пути к иконкам (data/icons)."""

from __future__ import annotations

from pathlib import Path

def icons_dir() -> Path:
    # Пакетный каталог (site-packages/linfix/data/icons), иначе репозиторий.
    local_dir = Path(__file__).resolve().parents[1] / "data" / "icons"
    if local_dir.exists():
        return local_dir
    return Path(__file__).resolve().parents[3] / "data" / "icons"


def icon_path(name: str) -> str | None:
    path = icons_dir() / f"{name}.svg"
    if path.exists():
        return str(path)
    return None


def tray_icon_path(state: str) -> str | None:
    """Иконка трея: ok/warn/fail (зелёная/жёлтая/красная)."""

    return icon_path(f"tray-{state}") or icon_path("linfix")
