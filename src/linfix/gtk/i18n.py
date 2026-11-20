"""Локализация: gettext, ru по умолчанию, en fallback."""

from __future__ import annotations

import gettext
from pathlib import Path

def _find_locale_dir() -> Path:
    local_dir = Path(__file__).resolve().parents[1] / "data" / "locale"
    if local_dir.exists():
        return local_dir
    repo_dir = Path(__file__).resolve().parents[3] / "data" / "locale"
    if repo_dir.exists():
        return repo_dir
    return local_dir


LOCALE_DIR = _find_locale_dir()
_translator = gettext.gettext


def _(text: str) -> str:
    """Диспетчер: `install()` меняет перевод, не требуя переимпорта `_`."""

    return _translator(text)


def install(lang: str | None = None) -> None:
    """Установить активный перевод: 'ru' | 'en'.

    Если `lang` не задан — читаем `[general] language` из LinFix, fallback — ru.
    """

    global _translator

    if lang is None:
        from ..config import load_config

        try:
            lang = load_config().general.language
        except Exception:
            lang = "ru"

    if lang not in {"ru", "en"}:
        lang = "ru"

    try:
        translator = gettext.translation(
            "linfix", str(LOCALE_DIR), languages=[lang, "ru", "en"], fallback=True
        )
        _translator = translator.gettext
    except Exception:
        _translator = gettext.gettext
