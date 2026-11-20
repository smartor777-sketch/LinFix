"""Guard: защита от шторма соединений при мёртвом CF-пути (M3).

Здесь — чистые функции без побочных эффектов, чтобы их можно было покрыть
unit-тестами до появления реального цикла чтения лога.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

CF_FAILURE_RE = re.compile(r"CF proxy failed", re.IGNORECASE)


@dataclass(frozen=True)
class GuardState:
    enabled: bool
    failures: int
    threshold: int
    window_seconds: int
    tls_ok: bool
    tripped: bool


def count_cf_failures(lines: Iterable[str]) -> int:
    return sum(1 for line in lines if CF_FAILURE_RE.search(line))


def should_trip(failures: int, threshold: int, tls_ok: bool) -> bool:
    """Останавливать прокси только при лавине ошибок И провале TLS-теста."""

    if threshold <= 0:
        return False
    return failures >= threshold and not tls_ok


def evaluate(
    lines: Iterable[str],
    *,
    threshold: int = 20,
    tls_ok: bool = True,
    window_seconds: int = 60,
    enabled: bool = True,
) -> GuardState:
    failures = count_cf_failures(lines)
    tripped = enabled and should_trip(failures, threshold, tls_ok)
    return GuardState(
        enabled=enabled,
        failures=failures,
        threshold=threshold,
        window_seconds=window_seconds,
        tls_ok=tls_ok,
        tripped=tripped,
    )
