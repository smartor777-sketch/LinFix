"""Потоковая обёртка для долгих операций + безопасный UI update через GLib.idle_add."""

from __future__ import annotations

import threading
from typing import Any, Callable

from gi.repository import GLib


def run_in_thread(
    fn: Callable[..., Any],
    *,
    on_done: Callable[[Any], None] | None = None,
    on_error: Callable[[Exception], None] | None = None,
    **kwargs: Any,
) -> threading.Thread:
    """Запустить функцию в потоке, после — on_done/on_error через GLib.idle_add."""

    def target() -> None:
        result: Any = None
        error: Exception | None = None
        try:
            result = fn(**kwargs)
        except Exception as exc:  # noqa: BLE001 — GUI-уровень: эвристика
            error = exc

        def dispatch() -> bool:
            if error is not None and on_error is not None:
                on_error(error)
            elif on_done is not None:
                on_done(result)
            return False

        if on_done is not None or on_error is not None:
            GLib.idle_add(dispatch)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    return thread
