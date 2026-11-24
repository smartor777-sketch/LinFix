"""Временное применение стратегии desync к zapret — для проверки качества.

Зачем: `blockcheck2` проверяет стратегию одноразовым GET. Это ловит
«соединение открылось», но не ловит поломку больших тел — именно так
`tcpseg` проходил подбор, а медиа затем не грузилось.

Чтобы проверить кандидата по-настоящему (H2-телом 256 КБ), его надо
применить к zapret. Здесь собрано применение с гарантированным откатом:

1. сохранить текущий конфиг;
2. подставить профиль кандидата;
3. перезапустить zapret;
4. …проверка выполняется снаружи…
5. вернуть конфиг и перезапустить.

Откат обязателен: без него система останется с чужим профилем.
"""

from __future__ import annotations

import shutil
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from . import util

DEFAULT_CONFIG = Path("/opt/zapret2/config")
DEFAULT_SERVICE = "zapret2"

#: Маркеры блока профилей в конфиге zapret.
OPT_START = 'NFQWS2_OPT="\n'
OPT_END = '\n"\n'


class ApplyError(util.NetfixError):
    """Не удалось применить или откатить стратегию."""


def build_profile_line(
    desync: list[str],
    *,
    hostlist: str,
    port: str = "443",
    layer: str = "tls",
    payload: str = "tls_client_hello",
) -> str:
    """Собрать строку профиля zapret из параметров desync.

    Стратегия из `blockcheck2` уже содержит нужные `--lua-desync=…`, но нет
    фильтров: их добавляем сами, чтобы правило действовало только на наш
    hostlist и только на TLS.
    """

    tokens = [token for token in desync if token.startswith("--")]
    if not tokens:
        raise ApplyError("в стратегии нет параметров --lua-desync")

    # blockcheck2 уже передаёт --payload в строке стратегии: дублировать
    # нельзя, nfqws2 на двух --payload ведёт себя непредсказуемо.
    has_payload = any(token.startswith("--payload=") for token in tokens)
    head = f"--filter-tcp={port} --filter-l7={layer} --hostlist={hostlist}"
    if not has_payload:
        head += f" --payload={payload}"
    return f"{head} {' '.join(tokens)} --new"


def read_profile_block(config_path: str | Path = DEFAULT_CONFIG) -> str:
    """Содержимое блока NFQWS2_OPT (без кавычек и завершающей пустой строки)."""

    text = Path(config_path).read_text(encoding="utf-8")
    start = text.find(OPT_START)
    if start < 0:
        raise ApplyError("в конфиге нет блока NFQWS2_OPT")
    start += len(OPT_START)
    end = text.find(OPT_END, start)
    if end < 0:
        raise ApplyError("не найден конец блока NFQWS2_OPT")
    return text[start:end]


def replace_profile_block(
    line: str, config_path: str | Path = DEFAULT_CONFIG
) -> str:
    """Поставить `line` первым правилом, остальные сохранить. Вернуть прежний блок."""

    path = Path(config_path)
    text = path.read_text(encoding="utf-8")
    start = text.find(OPT_START)
    if start < 0:
        raise ApplyError("в конфиге нет блока NFQWS2_OPT")
    start += len(OPT_START)
    end = text.find(OPT_END, start)
    if end < 0:
        raise ApplyError("не найден конец блока NFQWS2_OPT")

    previous = text[start:end]
    # Кандидат ставится первым: nfqws2 берёт первое совпавшее правило.
    updated = line + "\n" + previous
    util.atomic_write(path, text[:start] + updated + text[end:])
    return previous


def restore_profile_block(
    previous: str, config_path: str | Path = DEFAULT_CONFIG
) -> None:
    """Вернуть блок профилей в прежнее состояние."""

    path = Path(config_path)
    text = path.read_text(encoding="utf-8")
    start = text.find(OPT_START)
    if start < 0:
        raise ApplyError("в конфиге нет блока NFQWS2_OPT")
    start += len(OPT_START)
    end = text.find(OPT_END, start)
    if end < 0:
        raise ApplyError("не найден конец блока NFQWS2_OPT")
    util.atomic_write(path, text[:start] + previous + text[end:])


def restart(service: str = DEFAULT_SERVICE, *, timeout: float = 90.0) -> bool:
    """Перезапустить zapret и дождаться active."""

    util.run(["systemctl", "restart", service], check=False, timeout=timeout)
    deadline = timeout
    steps = int(deadline / 0.5)
    for _ in range(max(steps, 1)):
        state = util.run(["systemctl", "is-active", service], check=False, timeout=10)
        if (state.stdout or "").strip() == "active":
            return True
        time.sleep(0.5)
    return False


def hostlist_for_test(config_path: str | Path = DEFAULT_CONFIG) -> str | None:
    """Первый hostlist из конфига — в него и направим тестовое правило.

    Именно он определяет, к каким доменам применится профиль. Берём тот,
    что уже используется рабочим профилем CF.
    """

    try:
        block = read_profile_block(config_path)
    except ApplyError:
        return None
    for line in block.splitlines():
        for token in line.split():
            if token.startswith("--hostlist="):
                return token.split("=", 1)[1]
    return None


@contextmanager
def applied(
    desync: list[str],
    *,
    config_path: str | Path = DEFAULT_CONFIG,
    service: str = DEFAULT_SERVICE,
    hostlist: str | None = None,
) -> Iterator[str]:
    """Применить стратегию на время блока и гарантированно откатить.

    Откат выполняется даже при исключении внутри блока — иначе система
    осталась бы с тестовым профилем.
    """

    target = hostlist or hostlist_for_test(config_path)
    if not target:
        raise ApplyError("не найден hostlist для тестового профиля")

    line = build_profile_line(desync, hostlist=target)
    backup_path = Path(str(config_path) + ".linfix-apply")
    shutil.copy2(config_path, backup_path)

    previous = replace_profile_block(line, config_path)
    restarted = restart(service)
    if not restarted:
        # Не оставляем систему с тестовым профилем и нерабочей службой.
        restore_profile_block(previous, config_path)
        restart(service)
        raise ApplyError(f"{service} не поднялся с тестовым профилем — профиль возвращён")

    try:
        yield line
    finally:
        try:
            restore_profile_block(previous, config_path)
            restart(service)
        finally:
            # Файл-копия нужна только как страховка при сбое записи.
            backup_path.unlink(missing_ok=True)
