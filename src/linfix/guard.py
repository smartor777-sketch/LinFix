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

from . import cfdomains, cfprofile, netstate, state, tgws, util
from .config import Config

CF_FAILURE_RE = re.compile(r"CF proxy failed", re.IGNORECASE)
# H2-путь к CF может открываться, но не отдавать заголовки ответа.
# В verbose-режиме это видно как "phase=headers" и "down=0"; без него —
# как серия "H2 ... request failed" с таймаутом/обрывом записи.
H2_STALL_RE = re.compile(
    r"H2 DETAIL CLOSE lane=\d+ \[[^\]]*phase=headers[^\]]*down=0\b",
    re.IGNORECASE,
)
H2_FAILURE_RE = re.compile(
    r"H2 .*?request failed: (?:ReadTimeout|WriteError|RemoteProtocolError|ConnectTimeout)",
    re.IGNORECASE,
)
H2_STALL_MIN = 5


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
    status: str  # ok | warn | disabled | tripped | fallback
    detail: str
    failures: int
    threshold: int
    tripped: bool = False
    tls_ok: bool | None = None
    stopped: bool | None = None
    log_path: str | None = None
    h2_disabled: bool = False
    ipv6_blackholes: int = 0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def count_cf_failures(lines: Iterable[str]) -> int:
    return sum(1 for line in lines if CF_FAILURE_RE.search(line))


def count_h2_stalls(lines: Iterable[str]) -> int:
    """Сколько H2-каналов закрылось, не получив заголовки ответа."""

    return sum(1 for line in lines if H2_STALL_RE.search(line))


def count_h2_failures(lines: Iterable[str]) -> int:
    """Серия таймаутов H2-запросов — косвенный признак зависшего H2-пути."""

    return sum(1 for line in lines if H2_FAILURE_RE.search(line))


def should_disable_h2(stalls: int, h2_enabled: bool, *, failures: int = 0) -> bool:
    """Отключать H2 только при серии подтверждённых зависаний."""

    return h2_enabled and (stalls >= H2_STALL_MIN or failures >= H2_STALL_MIN)


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


def _h2_enabled(home: str | Path | None) -> bool:
    path = tgws.find_config_path(home)
    if path is None:
        return False
    try:
        return tgws.load_json_config(path).get("h2") is not False
    except tgws.TgwsError:
        return False


def h2_works_now(home: str | Path | None = None, *, timeout: float = 6.0) -> bool:
    """Проверить H2 живым запросом: TLS + POST 256 КБ к CF-домену."""

    path = tgws.find_config_path(home)
    domains: list[str] = []
    if path is not None:
        try:
            data = tgws.load_json_config(path)
        except tgws.TgwsError:
            data = {}
        value = data.get("cfproxy_user_domain")
        if isinstance(value, list):
            domains = [item for item in value if isinstance(item, str) and item]
    if not domains:
        domains = list(cfprofile_fallback_domains())

    try:
        report = cfprofile.check_profile(domains, limit=1, timeout=timeout)
    except Exception:
        return False
    return bool(report.body_ok)


def cfprofile_fallback_domains() -> tuple[str, ...]:
    return tuple(cfdomains.FALLBACK_DOMAINS)


def _disable_h2(home: str | Path | None, stalls: int, cf_failures: int, cfg: Config) -> GuardReport:
    """Переключить TgWsProxy на WS-CF и вернуть отчёт без остановки службы."""

    path = tgws.find_config_path(home)
    detail_prefix = (
        f"H2-путь к CF завис: {stalls} каналов без заголовков за окно. "
    )
    if path is None:
        report = GuardReport(
            "warn",
            detail_prefix + "конфиг TgWsProxy не найден — H2 не отключён",
            cf_failures,
            cfg.guard.error_threshold,
        )
        _save_event(home, report)
        return report

    try:
        data, backup, changed = tgws.update_config(path, {"h2": False})
    except tgws.TgwsError as exc:
        report = GuardReport(
            "warn",
            detail_prefix + f"не удалось отключить H2: {exc}",
            cf_failures,
            cfg.guard.error_threshold,
        )
        _save_event(home, report)
        return report

    restarted = util.run(
        ["systemctl", "--user", "restart", tgws.UNIT_NAME], check=False, timeout=60
    )
    restart_ok = restarted.returncode == 0
    detail = detail_prefix + (
        "H2 отключён, медиа переключено на WS-CF."
        if changed
        else "H2 уже был отключён; перезапуск для сброса каналов."
    )
    if not restart_ok:
        detail += " (не удалось перезапустить tgwsproxy — проверьте вручную)"
    report = GuardReport(
        "fallback",
        detail,
        cf_failures,
        cfg.guard.error_threshold,
        log_path=str(tgws.find_log_path(home) or ""),
        h2_disabled=True,
    )
    payload = {
        "last_trip": datetime.now().astimezone().isoformat(timespec="seconds"),
        **report.to_dict(),
        "backup": str(backup) if backup else None,
        "restarted": restart_ok,
        "h2_config": data.get("h2"),
    }
    util.atomic_write(
        event_path(home),
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        mode=0o600,
    )
    return report


def _ipv6_blackhole_count() -> int:
    """Сколько маршрутов-заглушек IPv6 висит сейчас.

    Читается без root: `ip -6 route show` доступен обычному пользователю.
    """

    return len(netstate.ipv6_blackhole_routes())


def check_guard(
    cfg: Config,
    *,
    home: str | Path | None = None,
    tls_test: Callable[[], list[tuple[str, bool, str]]] | None = None,
    stop_service: Callable[[], object] | None = None,
    skip_history: bool = True,
    h2_probe: Callable[[], bool] | None = None,
) -> GuardReport:
    if not cfg.guard.enabled:
        return GuardReport("disabled", "guard выключен в конфиге", 0, cfg.guard.error_threshold)

    # Висячие IPv6-заглушки проверяются первыми: пока весь IPv6 уходит в
    # blackhole, любые сетевые замеры врут, а пользователь видит «сайты не
    # открываются» без внятной причины. Но если VPN ещё работает, маршруты
    # принадлежат ему (kill-switch) — это не находка, а нормальное состояние.
    blackholes = _ipv6_blackhole_count()
    if blackholes and not netstate.vpn_interfaces():
        report = GuardReport(
            "warn",
            f"висячих IPv6-заглушек: {blackholes} — IPv6 уходит в никуда. "
            "Убрать: linfix net cleanup (или кнопка в «Диагностике»)",
            0,
            cfg.guard.error_threshold,
            ipv6_blackholes=blackholes,
        )
        # Событие сохраняем, иначе находку не увидит раздел «Диагностика».
        _save_event(home, report)
        return report

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
    # Первый запуск: не разбирать историю лога — иначе старые зависания H2
    # сразу же отключат рабочий H2. Начинаем с текущего конца файла.
    if cursor is None and skip_history:
        try:
            current = Path(log_path).stat()
        except OSError:
            current = None
        if current is not None:
            cursor = Cursor(
                path=str(log_path), offset=current.st_size, inode=current.st_ino
            )
            save_cursor(cursor_file, cursor)
    lines, new_cursor = read_new_lines(log_path, cursor)
    save_cursor(cursor_file, new_cursor)

    failures = count_cf_failures(lines)
    stalls = count_h2_stalls(lines)
    h2_failures = count_h2_failures(lines)
    if should_disable_h2(stalls, _h2_enabled(home), failures=h2_failures):
        # Перед отключением H2 проверяем его живым запросом: строки в логе
        # могли относиться к прошлому профилю zapret, а H2 сейчас работает.
        probe = h2_probe or (lambda: h2_works_now(home))
        if probe():
            report = GuardReport(
                "warn",
                f"в логе {stalls + h2_failures} зависаний H2, но живой запрос "
                "проходит — H2 не отключаю",
                failures,
                cfg.guard.error_threshold,
                log_path=str(log_path),
            )
            _save_event(home, report)
            return report
        return _disable_h2(home, stalls + h2_failures, failures, cfg)

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
