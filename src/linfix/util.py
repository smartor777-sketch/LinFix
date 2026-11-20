"""Общие утилиты: запуск команд, атомарная запись, бэкапы, логирование."""

from __future__ import annotations

import json
import logging
import os
import platform
import shlex
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Iterable, Sequence

LOG = logging.getLogger("linfix")


class NetfixError(Exception):
    """Базовая ошибка LinFix."""


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
    tag: str = "linfix",
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


def atomic_copy(source: str | Path, target: str | Path, *, mode: int = 0o755) -> None:
    """Атомарно скопировать файл (потоково, без чтения целиком в память)."""

    src = Path(source)
    dst = Path(target)
    dst.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{dst.name}.", suffix=".tmp", dir=str(dst.parent))
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(fd, "wb") as out_handle, src.open("rb") as in_handle:
            shutil.copyfileobj(in_handle, out_handle, length=1024 * 1024)
            out_handle.flush()
            os.fsync(out_handle.fileno())
        os.chmod(tmp_path, mode)
        os.replace(tmp_path, dst)
    except BaseException:
        try:
            tmp_path.unlink()
        except OSError:
            pass
        raise


def restore_file(backup: str | Path, target: str | Path) -> None:
    """Восстановить файл из бэкапа атомарно, с сохранением прав бэкапа."""

    source = Path(backup)
    if not source.exists():
        raise NetfixError(f"бэкап не найден: {source}")
    data = source.read_bytes()
    mode = source.stat().st_mode & 0o777
    atomic_write(target, data, mode=mode)
    LOG.warning("восстановлен файл %s из %s", target, source)


def helper_command() -> list[str]:
    """Команда запуска helper.

    ``LINFIX_HELPER`` — явное переопределение (dev/тесты). Иначе ищем
    установленный ``linfix-helper``; в dev-режиме запускаем модуль текущим
    интерпретатором.
    """

    override = os.environ.get("LINFIX_HELPER")
    if override:
        return shlex.split(override)
    installed = which("linfix-helper")
    if installed:
        return [installed]
    return [sys.executable, "-m", "linfix.helper"]


def _parse_helper_payload(stdout: str, stderr: str) -> dict[str, object]:
    for stream in (stdout, stderr):
        text = (stream or "").strip()
        if not text:
            continue
        try:
            payload = json.loads(text)
        except json.JSONDecodeError:
            start = text.find("{")
            end = text.rfind("}")
            if start < 0 or end <= start:
                continue
            try:
                payload = json.loads(text[start : end + 1])
            except json.JSONDecodeError:
                continue
        if isinstance(payload, dict):
            return payload
    return {}


def run_helper(
    action: str,
    args: Sequence[str],
    *,
    timeout: float = 120.0,
) -> tuple[int, dict[str, object]]:
    """Вызвать привилегированный helper и разобрать его JSON-ответ.

    Возвращает (код возврата, payload). Исключения NetfixError — если helper
    не установлен и эскалация невозможна.
    """

    if is_root() or os.environ.get("LINFIX_HELPER"):
        cmd = helper_command() + [action, *[str(item) for item in args]]
    else:
        installed = which("linfix-helper")
        if installed is None:
            if action.startswith("domains."):
                example = "sudo linfix domains update"
            elif action.startswith("tgws."):
                example = "sudo linfix tgws install"
            else:
                example = "sudo linfix"
            raise NetfixError(
                f"linfix-helper не установлен. Запустите через sudo: {example}"
            )
        if which("pkexec"):
            cmd = ["pkexec", installed, action, *[str(item) for item in args]]
        elif which("sudo"):
            cmd = ["sudo", installed, action, *[str(item) for item in args]]
        else:
            raise NetfixError("нет pkexec/sudo для привилегированной операции")

    completed = run(cmd, check=False, timeout=timeout)
    payload = _parse_helper_payload(completed.stdout, completed.stderr)
    if not payload:
        payload = {
            "ok": completed.returncode == 0,
            "error": (completed.stderr or completed.stdout or "").strip(),
        }
    return completed.returncode, payload


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
