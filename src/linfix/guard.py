"""Guard: защита от шторма соединений при мёртвом CF-пути.

Логика из SPEC 12:
- раз в тик читать только новые строки лога TgWsProxy (offset, не весь файл);
- считать ``CF proxy failed`` за окно;
- при превышении порога проверить TLS-доступность CF-доменов;
- останавливать прокси только если ошибки И TLS-тест провален.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable, Iterable

from . import cfdomains, state, tgws, util
from .config import Config

CF_FAILURE_RE = re.compile(r"CF proxy failed", re.IGNORECASE)


@dataclass(frozen=True)
class GuardState:
    enabled: bool
    failures: int
    threshold: int
    window_seconds: int
    tls_ok: bool
    tripped: bool


@dataclass(frozen=True)
class Cursor:
    path: str
    offset: int
    inode: int


@dataclass(frozen=True)
class GuardReport:
    status: str  # ok | warn | disabled | tripped
    detail: str
    failures: int
    threshold: int
    tripped: bool = False
    tls_ok: bool | None = None
    stopped: bool | None = None
    log_path: str | None = None

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


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


def timer_unit() -> str:
    return "linfix-guard.timer"


def ensure_timer(enabled: bool) -> dict[str, object]:
    """Включить/выключить user-таймер Guard.

    Флага в конфиге недостаточно: без `linfix-guard.timer` проверки
    никогда не запускаются.
    """

    action = "start" if enabled else "stop"
    util.run(["systemctl", "--user", action, timer_unit()], check=False, timeout=20)
    switch = "enable" if enabled else "disable"
    completed = util.run(
        ["systemctl", "--user", switch, timer_unit()], check=False, timeout=20
    )
    active = util.run(
        ["systemctl", "--user", "is-enabled", timer_unit()], check=False, timeout=10
    )
    return {
        "timer": timer_unit(),
        "timer_ok": completed.returncode == 0,
        "timer_state": (active.stdout or "").strip() or "unknown",
        "timer_error": (completed.stderr or "").strip(),
    }


def state_dir(home: str | Path | None = None) -> Path:
    base = Path(home) if home is not None else Path.home()
    return base / ".local" / "state" / "linfix"


def cursor_path(home: str | Path | None = None) -> Path:
    return state_dir(home) / "guard.json"


def event_path(home: str | Path | None = None) -> Path:
    return state_dir(home) / "guard-state.json"


def load_cursor(path: str | Path) -> Cursor | None:
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    path_value = raw.get("path")
    offset = raw.get("offset")
    inode = raw.get("inode")
    if not isinstance(path_value, str) or not isinstance(offset, int) or not isinstance(inode, int):
        return None
    return Cursor(path=path_value, offset=offset, inode=inode)


def save_cursor(path: str | Path, cursor: Cursor) -> None:
    util.atomic_write(
        path,
        json.dumps(asdict(cursor), ensure_ascii=False) + "\n",
        mode=0o600,
    )


def read_new_lines(
    log_path: str | Path,
    cursor: Cursor | None,
) -> tuple[list[str], Cursor]:
    """Прочитать только новые строки с учётом offset и ротации/truncate."""

    target = Path(log_path)
    stat = target.stat()
    offset = 0
    if (
        cursor is not None
        and cursor.path == str(target)
        and cursor.inode == stat.st_ino
        and cursor.offset <= stat.st_size
    ):
        offset = cursor.offset

    with target.open("rb") as handle:
        handle.seek(offset)
        payload = handle.read()
    lines = payload.decode("utf-8", "replace").splitlines()
    return lines, Cursor(path=str(target), offset=offset + len(payload), inode=stat.st_ino)


def load_event(home: str | Path | None = None) -> dict[str, object]:
    try:
        raw = json.loads(event_path(home).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _save_event(home: str | Path | None, report: GuardReport) -> None:
    payload = {
        "last_trip": datetime.now().astimezone().isoformat(timespec="seconds"),
        **report.to_dict(),
    }
    util.atomic_write(
        event_path(home),
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        mode=0o600,
    )


def check_guard(
    cfg: Config,
    *,
    home: str | Path | None = None,
    tls_test: Callable[[], list[tuple[str, bool, str]]] | None = None,
    stop_service: Callable[[], object] | None = None,
) -> GuardReport:
    if not cfg.guard.enabled:
        return GuardReport("disabled", "guard выключен в конфиге", 0, cfg.guard.error_threshold)

    log_path = tgws.find_log_path(home)
    if log_path is None:
        return GuardReport(
            "warn",
            "лог TgWsProxy не найден — guard не может считать ошибки",
            0,
            cfg.guard.error_threshold,
        )

    cursor_file = cursor_path(home)
    cursor = load_cursor(cursor_file)
    lines, new_cursor = read_new_lines(log_path, cursor)
    save_cursor(cursor_file, new_cursor)

    failures = count_cf_failures(lines)
    threshold = cfg.guard.error_threshold
    if failures < threshold:
        return GuardReport(
            "ok",
            f"CF proxy failed: {failures} < {threshold}",
            failures,
            threshold,
            log_path=str(log_path),
        )

    tester = tls_test or (
        lambda: cfdomains.test_domains(
            state.load_state().domains or cfdomains.FALLBACK_DOMAINS,
            limit=3,
        )
    )
    results = tester()
    tls_ok = bool(results) and all(ok for _, ok, _ in results)
    if tls_ok:
        return GuardReport(
            "warn",
            f"{failures} ошибок за окно, но TLS-тест проходит — прокси не останавливаем",
            failures,
            threshold,
            tls_ok=True,
            log_path=str(log_path),
        )

    stopper = stop_service or (
        lambda: util.run(["systemctl", "--user", "stop", tgws.UNIT_NAME], check=False, timeout=30)
    )
    stopped_result = stopper()
    stopped = getattr(stopped_result, "returncode", 0) == 0
    detail = (
        f"CF-путь недоступен: {failures} ошибок за окно, TLS-тест провален. "
        "Прокси остановлен, чтобы не перегрузить сеть. "
        "Запустите `linfix domains update` и проверьте `linfix domains test`."
    )
    if not stopped:
        detail += " (не удалось остановить службу — проверьте вручную)"

    report = GuardReport(
        "tripped",
        detail,
        failures,
        threshold,
        tripped=True,
        tls_ok=False,
        stopped=stopped,
        log_path=str(log_path),
    )
    _save_event(home, report)
    return report
