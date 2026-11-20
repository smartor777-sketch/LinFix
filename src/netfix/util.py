"""Общие утилиты: запуск команд, атомарная запись, бэкапы, логирование."""

from __future__ import annotations

import logging
import os
import platform
import shutil
import subprocess
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

LOG = logging.getLogger("netfix")


class NetfixError(Exception):
    """Базовая ошибка NetFix."""


class CommandError(NetfixError):
    """Команда завершилась с ошибкой или не запустилась."""

    def __init__(
        self,
        cmd: Sequence[str],
        returncode: int,
        stdout: str = "",
        stderr: str = "",
        message: str | None = None,
    ) -> None:
        self.cmd = list(cmd)
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr
        text = message or f"команда завершилась с кодом {returncode}: {' '.join(self.cmd)}"
        if stderr.strip():
            text += f"\n{stderr.strip()}"
        super().__init__(text)


def run(
    cmd: Sequence[str],
    *,
    check: bool = True,
    timeout: float = 30.0,
    env: dict[str, str] | None = None,
    input: str | None = None,
) -> subprocess.CompletedProcess[str]:
    """Запустить команду без shell, с таймаутом и захватом вывода.

    Никогда не использовать shell=True: все аргументы передаются списком.
    """

    argv = [str(part) for part in cmd]
    try:
        completed = subprocess.run(
            argv,
            text=True,
            capture_output=True,
            timeout=timeout,
            env=env,
            input=input,
        )
    except FileNotFoundError as exc:
        raise CommandError(argv, 127, stderr=str(exc), message=f"команда не найдена: {argv[0]}") from exc
    except subprocess.TimeoutExpired as exc:
        stdout = exc.stdout or ""
        if isinstance(stdout, bytes):
            stdout = stdout.decode("utf-8", "replace")
        raise CommandError(
            argv,
            124,
            stdout=stdout,
            stderr="",
            message=f"таймаут {timeout:g} с: {' '.join(argv)}",
        ) from exc

    if check and completed.returncode != 0:
        raise CommandError(argv, completed.returncode, completed.stdout, completed.stderr)
    return completed


def which(name: str) -> str | None:
    """Путь к исполняемому файлу или None."""

    return shutil.which(name)


def is_root() -> bool:
    return os.geteuid() == 0


def timestamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def backup_file(
    path: str | Path,
    *,
    tag: str = "netfix",
    backup_dir: str | Path | None = None,
) -> Path:
    """Сделать копию файла: ``<name>.bak-<tag>-YYYYMMDD-HHMMSS``.

    Один вызов = один бэкап. При совпадении имени добавляется числовой суффикс.
    Метаданные (права, владелец, mtime) сохраняются через :func:`shutil.copy2`.
    """

    source = Path(path)
    if not source.exists():
        raise NetfixError(f"нечего бэкапить, файл не найден: {source}")

    target_dir = Path(backup_dir) if backup_dir is not None else source.parent
    target_dir.mkdir(parents=True, exist_ok=True)

    stem = f"{source.name}.bak-{tag}-{timestamp()}"
    candidate = target_dir / stem
    counter = 1
    while candidate.exists():
        candidate = target_dir / f"{stem}-{counter}"
        counter += 1

    shutil.copy2(source, candidate)
    LOG.info("бэкап: %s -> %s", source, candidate)
    return candidate


def atomic_write(
    path: str | Path,
    data: str | bytes,
    *,
    mode: int | None = None,
    encoding: str = "utf-8",
) -> None:
    """Атомарно записать файл: temp в той же директории -> os.replace.

    Если файл существует, по умолчанию сохраняются его права и владелец
    (владелец меняется только при запуске от root).
    """

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)

    existing_stat = None
    try:
        existing_stat = target.stat()
    except FileNotFoundError:
        pass

    if mode is None:
        mode = (existing_stat.st_mode & 0o777) if existing_stat is not None else 0o644

    payload = data.encode(encoding) if isinstance(data, str) else data

    fd, tmp_name = tempfile.mkstemp(prefix=f".{target.name}.", suffix=".tmp", dir=str(target.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp_path, mode)
        if existing_stat is not None and is_root():
            try:
                os.chown(tmp_path, existing_stat.st_uid, existing_stat.st_gid)
            except OSError:
                LOG.warning("не удалось сохранить владельца %s", target)
        os.replace(tmp_path, target)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def read_text(path: str | Path) -> str:
    return Path(path).read_text(encoding="utf-8", errors="replace")


def write_lines(path: str | Path, lines: Iterable[str], *, newline: str = "\n") -> None:
    text = newline.join(lines)
    if lines:
        text += newline
    atomic_write(path, text)


def setup_logging(level: str = "info") -> None:
    numeric = getattr(logging, str(level).upper(), logging.INFO)
    logging.basicConfig(
        level=numeric,
        format="%(levelname)s %(name)s: %(message)s",
    )


def os_pretty_name() -> str:
    """Человеческое имя дистрибутива из /etc/os-release."""

    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return platform.platform()


def human_bytes(size: int) -> str:
    units = ("B", "KiB", "MiB", "GiB")
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"
