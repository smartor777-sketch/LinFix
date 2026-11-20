"""Управление hostlist zapret: чтение, добавление, удаление, импорт.

LinFix не перезаписывает чужие строки: домены прокси живут в блоке с
маркером CF, пользовательские — в блоке с маркером пользователя.
Всё остальное в файле считается «чужим» и остаётся нетронутым.
"""

from __future__ import annotations

import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from . import cfdomains, util
from .config import DEFAULT_MARKER

#: Маркер блока доменов, добавленных пользователем через LinFix.
USER_MARKER = "# linfix: user domains"

DOMAIN_RE = re.compile(r"^[a-z0-9_](?:[a-z0-9_.-]*[a-z0-9_])?$")
MAX_IMPORT_BYTES = 8 * 1024 * 1024


class HostlistError(util.NetfixError):
    """Ошибка работы с hostlist."""


@dataclass(frozen=True)
class HostlistEntry:
    domain: str
    source: str  # cf | user | other


@dataclass(frozen=True)
class ImportResult:
    added: list[str]
    skipped: list[str]
    total: int


def is_domain(value: str) -> bool:
    return bool(value) and bool(DOMAIN_RE.match(value)) and "." in value


def normalize_domain(raw: str) -> str | None:
    value = raw.strip().lower()
    for prefix in ("http://", "https://"):
        if value.startswith(prefix):
            value = value[len(prefix) :]
    value = value.split("/")[0].split(":")[0].rstrip(".")
    if not is_domain(value):
        return None
    return value


def parse_list_text(text: str) -> list[str]:
    """Разобрать пользовательский список: по одному домену на строку."""

    result: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("//"):
            continue
        for token in re.split(r"[\s,;]+", stripped):
            domain = normalize_domain(token)
            if domain and domain not in seen:
                seen.add(domain)
                result.append(domain)
    return result


def read_entries(path: str | Path) -> list[HostlistEntry]:
    """Прочитать файл и отметить, откуда взялся каждый домен."""

    target = Path(path)
    if not target.exists():
        return []
    text = target.read_text(encoding="utf-8", errors="replace")
    entries: list[HostlistEntry] = []
    section = "other"
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            lowered = stripped.lower()
            if lowered.startswith(DEFAULT_MARKER.split("(")[0].strip().lower()):
                section = "cf"
            elif lowered.startswith(USER_MARKER):
                section = "user"
            continue
        if not stripped:
            continue
        entries.append(HostlistEntry(stripped, section))
    return entries


def _split_sections(path: Path) -> tuple[list[str], list[str], list[str]]:
    """Вернуть (чужие строки, строки блока CF, строки блока пользователя)."""

    if not path.exists():
        return [], [], []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    other: list[str] = []
    cf: list[str] = []
    user: list[str] = []
    section = "other"
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("#"):
            lowered = stripped.lower()
            if lowered.startswith(DEFAULT_MARKER.split("(")[0].strip().lower()):
                section = "cf"
            elif lowered.startswith(USER_MARKER):
                section = "user"
            else:
                other.append(line)
            continue
        if not stripped:
            continue
        if section == "cf":
            cf.append(stripped)
        elif section == "user":
            user.append(stripped)
        else:
            other.append(stripped)
    return other, cf, user


def fetch_list(url: str, *, timeout: float = 15.0) -> str:
    """Скачать список по HTTPS (как в NetFix: URL → текст → домены)."""

    if not url.lower().startswith("https://"):
        raise HostlistError("скачивание разрешено только по HTTPS")
    request = urllib.request.Request(url, headers={"User-Agent": "linfix/0.1"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            if not response.geturl().lower().startswith("https://"):
                raise HostlistError("редирект на не-HTTPS запрещён")
            payload = response.read(MAX_IMPORT_BYTES + 1)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise HostlistError(f"не удалось скачать список: {exc}") from exc
    if len(payload) > MAX_IMPORT_BYTES:
        raise HostlistError(f"список больше {MAX_IMPORT_BYTES} байт")
    return payload.decode("utf-8", "replace")


def write_hostlist(path: str | Path, *, other: list[str], cf: list[str], user: list[str]) -> Path | None:
    """Записать файл в порядке: чужие строки → блок CF → блок пользователя."""

    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = [line for line in other if line.strip()]
    if cf:
        lines.append(f"{DEFAULT_MARKER} (updated {__import__('datetime').date.today().isoformat()})")
        lines.extend(sorted(set(cf)))
    if user:
        lines.append(USER_MARKER)
        lines.extend(sorted(set(user)))
    text = "\n".join(lines) + ("\n" if lines else "")
    if target.exists() and target.read_text(encoding="utf-8", errors="replace") == text:
        return None
    backup = util.backup_file(target) if target.exists() else None
    util.atomic_write(target, text)
    return backup


def add_domains(path: str | Path, domains: list[str]) -> ImportResult:
    """Добавить домены в пользовательский блок (чужие строки не трогаем)."""

    target = Path(path)
    other, cf, user = _split_sections(target)
    known = {item.lower() for item in other + cf + user}
    added: list[str] = []
    skipped: list[str] = []
    for raw in domains:
        domain = normalize_domain(raw)
        if not domain:
            skipped.append(raw)
            continue
        if domain in known:
            skipped.append(domain)
            continue
        known.add(domain)
        added.append(domain)
    if added:
        write_hostlist(target, other=other, cf=cf, user=user + added)
    return ImportResult(added, skipped, len(other) + len(cf) + len(user) + len(added))


def remove_domains(path: str | Path, domains: list[str]) -> ImportResult:
    """Удалить домены из блоков CF и пользовательского (чужие не трогаем)."""

    target = Path(path)
    other, cf, user = _split_sections(target)
    wanted = {normalize_domain(item) or item.strip().lower() for item in domains}
    removed = [item for item in cf + user if item.lower() in wanted]
    cf = [item for item in cf if item.lower() not in wanted]
    user = [item for item in user if item.lower() not in wanted]
    if removed:
        write_hostlist(target, other=other, cf=cf, user=user)
    return ImportResult(removed, [], len(other) + len(cf) + len(user))


def list_sources(ipset_dir: str | Path) -> list[tuple[str, str]]:
    """Доступные в zapret скрипты-источники списков (имя, описание)."""

    directory = Path(ipset_dir)
    known = {
        "get_antizapret_domains.sh": "Antizapret: список доменов",
        "get_reestr_resolvable_domains.sh": "Реестр: домены, которые резолвятся",
        "get_refilter_domains.sh": "Refilter: список доменов",
        "get_hostlist.sh": "Пользовательский hostlist",
        "get_user.sh": "Только пользовательский список",
    }
    if not directory.is_dir():
        return []
    result: list[tuple[str, str]] = []
    for name, description in known.items():
        if (directory / name).is_file():
            result.append((name, description))
    return result


def load_source(script: str, ipset_dir: str | Path) -> tuple[int, dict[str, object]]:
    """Запустить скрипт-источник zapret (нужен root: он пишет в ipset)."""

    directory = Path(ipset_dir)
    path = directory / script
    if not path.is_file():
        return 2, {"ok": False, "error": f"скрипт не найден: {path}"}
    completed = util.run(["sh", str(path)], check=False, timeout=900)
    output = ((completed.stdout or "") + (completed.stderr or "")).strip()
    if completed.returncode != 0:
        return 5, {"ok": False, "error": f"{script}: код {completed.returncode}", "output": output[-3000:]}
    return 0, {"ok": True, "script": script, "output": output[-3000:]}


def hostlist_for(cfg) -> str | None:
    """Путь к записываемому hostlist текущей установки zapret."""

    from . import zapret

    info = zapret.detect_zapret(cfg.zapret.service)
    return zapret.hostlist_target(info, cfg.zapret.hostlist)


__all__ = [
    "HostlistEntry",
    "HostlistError",
    "ImportResult",
    "USER_MARKER",
    "add_domains",
    "cfdomains",
    "fetch_list",
    "hostlist_for",
    "is_domain",
    "list_sources",
    "load_source",
    "normalize_domain",
    "parse_list_text",
    "read_entries",
    "remove_domains",
    "write_hostlist",
]
