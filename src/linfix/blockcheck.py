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

import os
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import util

#: Уровни сканирования blockcheck2: чем глубже, тем дольше.
SCAN_LEVELS = ("veryquick", "quick", "standard", "force")

RESULT_RE = re.compile(r"^(\d+)/(\d+)\s*:\s*(.+)$")


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


def parse_output(text: str) -> list[Strategy]:
    """Достать стратегии из вывода blockcheck2."""

    seen: set[str] = set()
    result: list[Strategy] = []
    for line in text.splitlines():
        match = RESULT_RE.match(line.strip())
        if not match:
            continue
        success, total, args = int(match.group(1)), int(match.group(2)), match.group(3).strip()
        if args in seen:
            continue
        seen.add(args)
        result.append(Strategy(success, total, args))
    result.sort(key=lambda item: (item.score, item.success), reverse=True)
    return result


def build_env(
    install_dir: str | Path, domains: list[str], level: str, *, ipv: str = "4"
) -> dict[str, str]:
    """Переменные окружения для неинтерактивного запуска."""

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
            "PARALLEL": "1",
            # не даём скрипту что-то скачивать и надолго зависать
            "PACE": "0",
        }
    )
    return env


def run_blockcheck(
    install_dir: str | Path,
    *,
    domains: list[str],
    level: str = "quick",
    timeout: float = 600.0,
) -> BlockcheckResult:
    """Запустить blockcheck2 и разобрать найденные стратегии (нужен root)."""

    if level not in SCAN_LEVELS:
        raise BlockcheckError(f"недопустимый уровень: {level}")
    path = script_path(install_dir)
    if not path.is_file():
        raise BlockcheckError(f"blockcheck2.sh не найден: {path}")

    env = build_env(install_dir, domains, level)
    completed = util.run(
        ["sh", str(path)],
        check=False,
        timeout=timeout,
        env=env,
    )
    output = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
    result = BlockcheckResult(domains=domains, level=level, output=output[-6000:])
    result.strategies = parse_output(output)
    result.works_without_bypass = "working without bypass" in output.lower()
    if completed.returncode != 0 and not result.strategies:
        result.error = f"blockcheck2 завершился с кодом {completed.returncode}"
        tail = output.strip().splitlines()[-3:]
        if tail:
            result.error += ": " + " ".join(tail)[:300]
    return result


def check_readiness(cfg) -> tuple[bool, str]:
    """Готова ли система к blockcheck: скрипт есть, zapret остановлен."""

    from . import zapret

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
