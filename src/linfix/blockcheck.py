"""Подбор рабочей стратегии desync через blockcheck2.sh.

Скрипт штатный, из самого zapret, но у него жёсткие требования:
- нужен root;
- **zapret должен быть остановлен** (иначе он сам мешает замерам);
- по умолчанию он спрашивает домены и уровень сканирования, но умеет
  работать в batch-режиме через переменные окружения.

Результат — строки вида
``4/4 : --filter-tcp=443 --hostlist=... --lua-desync=multisplit:...``
(чем больше совпадений, тем лучше стратегия).
"""

from __future__ import annotations

import json
import os
import re
import signal
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path

from . import util

#: Уровни сканирования blockcheck2: чем глубже, тем дольше.
SCAN_LEVELS = ("veryquick", "quick", "standard", "force")

RESULT_RE = re.compile(r"^(\d+)/(\d+)\s*:\s*(.+)$")
#: Строка успешного отчёта blockcheck2:
#: ``curl_test_https_tls12 ipv4 rutracker.org : nfqws2 --filter-tcp=443 …``
REPORT_RE = re.compile(r"^\S+ ipv\d \S+ : (.+)$")
#: Строка «найдена рабочая стратегия» (один домен, без блока COVERAGE).
FOUND_RE = re.compile(r"working strategy found for ipv\d \S+ : (.+?)\s*!+\s*$")

#: Маркер активного прогона. Создаёт helper (root), читает GUI и CLI.
#: Нужен, чтобы никто не поднял zapret посреди замеров: иначе результаты
#: недостоверны, а пользователь остаётся без обхода дольше нужного.
DEFAULT_LOCK = Path("/run/linfix/blockcheck.lock")

#: Результат последнего прогона (strategies/best) для показа в GUI.
#: Пишет helper (root), читает GUI как обычный пользователь.
DEFAULT_LAST_RESULT = Path("/run/linfix/blockcheck-last.json")


class BlockcheckError(util.NetfixError):
    """Не удалось запустить подбор стратегии."""


@dataclass
class Strategy:
    """Одна найденная стратегия: доля успешных доменов и аргументы."""

    success: int
    total: int
    args: str

    @property
    def score(self) -> float:
        return self.success / self.total if self.total else 0.0

    @property
    def desync(self) -> list[str]:
        """Только параметры desync — без hostlist и фильтров."""

        return [token for token in self.args.split() if "--lua-desync=" in token]


@dataclass
class BlockcheckResult:
    domains: list[str]
    level: str
    strategies: list[Strategy] = field(default_factory=list)
    output: str = ""
    error: str = ""
    #: Сайт открывается без обхода — подбирать нечего.
    works_without_bypass: bool = False

    @property
    def best(self) -> Strategy | None:
        if not self.strategies:
            return None
        return max(self.strategies, key=lambda item: (item.score, item.success))


def script_path(install_dir: str | Path) -> Path:
    return Path(install_dir) / "blockcheck2.sh"


def normalize_args(raw: str) -> str:
    """Оставить только параметры стратегии, начиная с первого ``--``.

    blockcheck2 печатает строку вида
    ``2/2 : curl_test_https_tls12 ipv4 : nfqws2 --filter-tcp=443 …``.
    В конфиг zapret вставляется только хвост ``--filter-tcp=443 …``;
    служебный префикс (тест, IP-версия, демон) пользователю не нужен.
    """

    text = raw.strip().rstrip("!").strip()
    pos = text.find("--")
    if pos < 0:
        return ""
    if pos > 0 and not text[pos - 1].isspace():
        return ""
    return text[pos:].strip()


def parse_output(text: str) -> list[Strategy]:
    """Достать стратегии из вывода blockcheck2.

    Поддерживаются три формата: блок ``COVERAGE`` (``N/M : …``), строки
    отчёта (``curl_test… ipv4 domain : nfqws2 …``) и сообщения
    ``working strategy found`` для одного домена.
    """

    found: dict[str, Strategy] = {}

    def add(success: int, total: int, raw: str) -> None:
        args = normalize_args(raw)
        if not args:
            return
        previous = found.get(args)
        if previous is None or (success / total) > (previous.success / previous.total):
            found[args] = Strategy(success, total, args)

    for line in text.splitlines():
        stripped = line.strip()
        match = RESULT_RE.match(stripped)
        if match:
            add(int(match.group(1)), int(match.group(2)), match.group(3))
            continue
        match = FOUND_RE.search(stripped)
        if match:
            add(1, 1, match.group(1))
            continue
        match = REPORT_RE.match(stripped)
        if match:
            add(1, 1, match.group(1))

    result = list(found.values())
    result.sort(key=lambda item: (item.score, item.success), reverse=True)
    return result


#: Сколько раз повторять каждый тест. При REPEATS=1 параллельный режим
#: в blockcheck2 не включается вообще, при 2+ — попытки идут одновременно.
DEFAULT_REPEATS = 2


def lock_file(path: Path | None = None) -> Path:
    """Путь к маркеру прогона (в тестах подменяется на временный)."""

    return Path(path) if path is not None else DEFAULT_LOCK


def pid_alive(pid: int) -> bool:
    """Жив ли процесс. PermissionError означает «жив, но чужой»."""

    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def read_lock(path: Path | None = None) -> dict[str, object]:
    try:
        data = json.loads(lock_file(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def write_lock(path: Path | None = None, **fields: object) -> Path:
    """Создать маркер прогона от имени текущего процесса."""

    target = lock_file(path)
    payload = {"pid": os.getpid(), "started": time.time(), **fields}
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        # Атомарно: GUI не должен прочитать недописанный JSON и решить,
        # что подбор уже закончился.
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.chmod(tmp, 0o644)
        os.replace(tmp, target)
    except OSError:
        # Маркер не критичен: сам подбор от этого не ломается.
        pass
    return target


def clear_lock(path: Path | None = None) -> None:
    try:
        lock_file(path).unlink()
    except OSError:
        pass


def last_result_path(path: Path | None = None) -> Path:
    """Путь к файлу с результатом последнего прогона (подменяется в тестах)."""

    return Path(path) if path is not None else DEFAULT_LAST_RESULT


def save_last_result(result: BlockcheckResult, path: Path | None = None) -> None:
    """Записать результат прогона для показа в GUI (подбор «сейчас что было»)."""

    target = last_result_path(path)
    payload = {
        "saved": time.time(),
        "domains": result.domains,
        "level": result.level,
        "error": result.error or "",
        "works_without_bypass": result.works_without_bypass,
        "strategies": [
            {"success": s.success, "total": s.total, "args": s.args}
            for s in result.strategies[:10]
        ],
    }
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        os.replace(tmp, target)
        os.chmod(target, 0o644)
    except OSError:
        pass


def read_last_result(path: Path | None = None) -> BlockcheckResult | None:
    """Прочитать результат последнего прогона, если он есть."""

    try:
        data = json.loads(last_result_path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    strategies = [
        Strategy(
            success=int(item.get("success", 0)),
            total=int(item.get("total", 0)),
            args=str(item.get("args", "")),
        )
        for item in data.get("strategies", [])
        if isinstance(item, dict)
    ]
    result = BlockcheckResult(
        domains=[str(d) for d in data.get("domains", [])],
        level=str(data.get("level", "")),
        strategies=strategies,
        error=str(data.get("error", "")),
        works_without_bypass=bool(data.get("works_without_bypass", False)),
    )
    return result


def running(path: Path | None = None) -> dict[str, object]:
    """Идёт ли подбор стратегии прямо сейчас.

    Возвращает данные маркера, только если создавший его процесс жив:
    после kill helper'а маркер остаётся, но считать его активным нельзя.
    """

    data = read_lock(path)
    try:
        pid = int(data.get("pid") or 0)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return {}
    return data if pid_alive(pid) else {}


def stray_processes() -> list[int]:
    """Процессы blockcheck2.sh, работающие без живого маркера.

    Такое бывает, если helper убили по внешнему таймауту: сам helper уже
    мёртв, а его дочерние оболочки ещё завершают тесты.
    """

    cp = util.run(
        ["pgrep", "-f", r"^sh .*/blockcheck2\.sh$"],
        check=False,
        timeout=5,
    )
    result: list[int] = []
    for line in (cp.stdout or "").split():
        try:
            pid = int(line)
        except ValueError:
            continue
        if pid != os.getpid():
            result.append(pid)
    return result


def scan_in_progress() -> bool:
    """Подбор идёт или хотя бы оставил после себя процессы."""

    return bool(running()) or bool(stray_processes())


def build_env(
    install_dir: str | Path,
    domains: list[str],
    level: str,
    *,
    ipv: str = "4",
    repeats: int = DEFAULT_REPEATS,
) -> dict[str, str]:
    """Переменные окружения для неинтерактивного запуска.

    ``REPEATS`` — ключевой параметр скорости: blockcheck2 включает
    параллельный режим только когда повторов больше одного (``PARALLEL``
    без этого просто игнорируется). Каждый вариант стратегии проверяется
    ``repeats`` раз одновременно, поэтому 2 повтора почти не удлиняют
    прогон, но отсекают нестабильные стратегии.
    """

    env = dict(os.environ)
    env.update(
        {
            "ZAPRET_BASE": str(install_dir),
            "ZAPRET_RW": str(install_dir),
            "ZAPRET_CONFIG": str(Path(install_dir) / "config"),
            "DOMAINS": " ".join(domains),
            "SCANLEVEL": level,
            "IPVS": ipv,
            "ENABLE_HTTP": "1",
            "BATCH": "1",
            "REPEATS": str(max(1, repeats)),
            "PARALLEL": "1" if repeats > 1 else "0",
            # не даём скрипту что-то скачивать и надолго зависать
            "PACE": "0",
        }
    )
    return env


def _stop_group(proc: subprocess.Popen[str], grace: float = 10.0) -> None:
    """Остановить всю группу blockcheck2: shell и его nfqws2-тесты.

    subprocess.run убивает только прямого потомка, а blockcheck2 запускает
    проверочные nfqws2 в своей группе. Без killpg они переживают таймаут и
    продолжают трогать nft-правила, пока идёт следующий прогон.
    """

    def kill(sig: int) -> None:
        try:
            os.killpg(proc.pid, sig)
        except (ProcessLookupError, PermissionError):
            pass

    kill(signal.SIGTERM)
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    kill(signal.SIGKILL)


def run_blockcheck(
    install_dir: str | Path,
    *,
    domains: list[str],
    level: str = "quick",
    timeout: float = 600.0,
    repeats: int = DEFAULT_REPEATS,
) -> BlockcheckResult:
    """Запустить blockcheck2 и разобрать найденные стратегии (нужен root)."""

    if level not in SCAN_LEVELS:
        raise BlockcheckError(f"недопустимый уровень: {level}")
    path = script_path(install_dir)
    if not path.is_file():
        raise BlockcheckError(f"blockcheck2.sh не найден: {path}")

    env = build_env(install_dir, domains, level, repeats=repeats)
    try:
        proc = subprocess.Popen(
            ["sh", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
            # Своя сессия: таймаут должен гасить всю группу, а не только sh.
            start_new_session=True,
        )
    except OSError as exc:
        raise BlockcheckError(f"не удалось запустить blockcheck2: {exc}") from exc

    timed_out = False
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        _stop_group(proc)
        try:
            stdout, stderr = proc.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            # Кто-то из внуков всё ещё держит пайп — не ждём его вечно.
            stdout = stderr = ""
            for stream in (proc.stdout, proc.stderr):
                try:
                    stream.close()  # type: ignore[union-attr]
                except (OSError, ValueError):
                    pass

    output = ((stdout or "") + "\n" + (stderr or "")).strip()
    result = BlockcheckResult(domains=domains, level=level, output=output[-6000:])
    result.strategies = parse_output(output)
    result.works_without_bypass = "working without bypass" in output.lower()
    if timed_out:
        result.error = (
            f"blockcheck2 прерван по таймауту {timeout:g} с — "
            "проверочные процессы остановлены, zapret возвращён в работу"
        )
    elif proc.returncode != 0 and not result.strategies:
        result.error = f"blockcheck2 завершился с кодом {proc.returncode}"
        tail = output.strip().splitlines()[-3:]
        if tail:
            result.error += ": " + " ".join(tail)[:300]
    return result


def check_readiness(cfg) -> tuple[bool, str]:
    """Готова ли система к blockcheck: скрипт есть, zapret остановлен."""

    from . import zapret

    if running():
        return False, "подбор стратегии уже идёт — дождитесь окончания"
    stray = stray_processes()
    if stray:
        pids = ", ".join(str(pid) for pid in stray[:3])
        return False, f"остались процессы blockcheck2.sh ({pids}) — дождитесь их завершения"
    info = zapret.detect_zapret(cfg.zapret.service)
    if not info.install_dir:
        return False, "zapret не установлен"
    if not script_path(info.install_dir).is_file():
        return False, f"blockcheck2.sh не найден в {info.install_dir}"
    if info.active:
        return (
            False,
            "для точных замеров zapret нужно остановить: он сам вмешивается "
            "в трафик, и результаты будут неверными",
        )
    return True, "готово"
