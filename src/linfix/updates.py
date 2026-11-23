"""Проверка и установка обновлений zapret2 и TgWsProxy.

Задача: сказать, что вышло новое, и обновить безопасно. Для zapret2
«безопасно» — значит сохранить наш конфиг и hostlists: штатный
install_easy.sh пишет в тот же ``config`` и затёр бы профили CF/rutracker
и список доменов.
"""

from __future__ import annotations

import json
import shutil
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from . import state, tgws, util, zapret

#: Репозиторий zapret2 для проверки новых коммитов.
ZAPRET_REPO = "bol-van/zapret2"
ZAPRET_COMMITS_API = f"https://api.github.com/repos/{ZAPRET_REPO}/commits?per_page=1"

#: Каталог, где лежат копии нашего конфига и списков (нужен root).
BACKUP_DIR = Path("/var/lib/linfix/zapret-profile")

#: Файлы, которые надо сохранить перед обновлением zapret2 и вернуть после.
#: Конфиг — наши профили; списки — домены, которые мы туда положили.
PROFILE_FILES = (
    "config",
    "ipset/linfix-cf-hosts.txt",
    "ipset/zapret-hosts-rut.txt",
)

#: Как часто проверять обновления в фоне.
CHECK_INTERVAL = timedelta(hours=24)

#: Файл с результатом последней проверки (доступен пользователю).
CACHE_NAME = "updates.json"


class UpdateError(util.NetfixError):
    """Не удалось проверить или установить обновление."""


@dataclass(frozen=True)
class Remote:
    """Что доступно в upstream."""

    zapret_commit: str | None = None
    zapret_date: str | None = None
    tgws_tag: str | None = None


@dataclass(frozen=True)
class Found:
    """Результат сравнения установленного с upstream."""

    zapret_current: str | None = None
    zapret_new: str | None = None
    tgws_current: str | None = None
    tgws_new: str | None = None
    checked_at: str | None = None
    error: str | None = None

    @property
    def has_zapret(self) -> bool:
        return bool(self.zapret_new and self.zapret_new != self.zapret_current)

    @property
    def has_tgws(self) -> bool:
        return bool(self.tgws_new and self.tgws_new != self.tgws_current)

    @property
    def any(self) -> bool:
        return self.has_zapret or self.has_tgws

    def summary(self) -> str:
        parts: list[str] = []
        if self.has_zapret:
            parts.append(f"zapret2 {self.zapret_new}")
        if self.has_tgws:
            parts.append(f"TgWsProxy {self.tgws_new}")
        return ", ".join(parts)


def _github_json(url: str, *, timeout: float = 20.0) -> object:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "linfix", "Accept": "application/vnd.github+json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.load(response)
    except Exception as exc:  # noqa: BLE001
        raise UpdateError(f"не удалось запросить GitHub: {exc}") from exc


def fetch_remote(*, timeout: float = 20.0) -> Remote:
    """Узнать последний коммит zapret2 и последний релиз TgWsProxy."""

    zapret_commit: str | None = None
    zapret_date: str | None = None
    try:
        commits = _github_json(ZAPRET_COMMITS_API, timeout=timeout)
        if isinstance(commits, list) and commits:
            first = commits[0]
            if isinstance(first, dict):
                zapret_commit = str(first.get("sha") or "")[:8] or None
                commit = first.get("commit")
                if isinstance(commit, dict):
                    committer = commit.get("committer")
                    if isinstance(committer, dict):
                        stamp = committer.get("date")
                        if isinstance(stamp, str):
                            zapret_date = stamp
    except UpdateError:
        zapret_commit = None

    tgws_tag: str | None = None
    try:
        tgws_tag = tgws.fetch_release(timeout=timeout).tag
    except tgws.TgwsError:
        tgws_tag = None

    if not zapret_commit and not tgws_tag:
        raise UpdateError("не удалось получить сведения об обновлениях")
    return Remote(
        zapret_commit=zapret_commit,
        zapret_date=zapret_date,
        tgws_tag=tgws_tag,
    )


def installed_zapret_marker(install_dir: str | Path | None = None) -> str | None:
    """Отпечаток установленного zapret2.

    Upstream не публикует релизы, поэтому сравниваем по времени установки
    бинарника: если последний коммит новее — считаем, что вышло обновление.
    Возвращаем ISO-дату установки бинарника.
    """

    base = Path(install_dir) if install_dir else None
    if base is None:
        info = zapret.detect_zapret("zapret2")
        if not info.found or not info.install_dir:
            return None
        base = Path(info.install_dir)
    candidates = [base / "nfq2" / "nfqws2", base / "binaries"]
    for path in candidates:
        if path.exists():
            return datetime.fromtimestamp(path.stat().st_mtime).astimezone().isoformat(
                timespec="seconds"
            )
    return None


def compare(remote: Remote, *, cfg_state: state.State | None = None) -> Found:
    current_state = cfg_state or state.load_state()
    zapret_installed = installed_zapret_marker()
    tgws_current = current_state.tgws_version
    if tgws_current is None and tgws.is_installed():
        tgws_current = "неизвестно"

    zapret_new: str | None = None
    if remote.zapret_commit and remote.zapret_date and zapret_installed:
        try:
            installed_at = datetime.fromisoformat(zapret_installed)
            remote_at = datetime.fromisoformat(
                remote.zapret_date.replace("Z", "+00:00")
            )
        except ValueError:
            installed_at = remote_at = None  # type: ignore[assignment]
        if installed_at is not None and remote_at is not None:
            if remote_at > installed_at + timedelta(minutes=5):
                zapret_new = remote.zapret_commit

    return Found(
        zapret_current=(zapret_installed or "")[:10] or None,
        zapret_new=zapret_new,
        tgws_current=tgws_current,
        tgws_new=remote.tgws_tag,
        checked_at=datetime.now().astimezone().isoformat(timespec="seconds"),
    )


def check(*, timeout: float = 20.0) -> Found:
    """Проверить обновления и записать результат в state.json."""

    remote = fetch_remote(timeout=timeout)
    found = compare(remote)
    save_found(found)
    return found


def cache_path(home: str | Path | None = None) -> Path:
    """Файл кэша в пользовательском каталоге.

    ``/var/lib/linfix`` доступен только root, а проверка обновлений —
    обычное пользовательское действие: кэш должен быть доступен на запись.
    """

    base = Path(home) if home is not None else Path.home()
    return base / ".local" / "state" / "linfix" / CACHE_NAME


def save_found(found: Found, *, home: str | Path | None = None) -> None:
    """Запомнить результат, чтобы проверка не повторялась каждый запуск."""

    path = cache_path(home)
    payload = json.dumps(
        {
            "checked_at": found.checked_at,
            "zapret_current": found.zapret_current,
            "zapret_new": found.zapret_new,
            "tgws_current": found.tgws_current,
            "tgws_new": found.tgws_new,
        },
        ensure_ascii=False,
        indent=2,
    ) + "\n"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        util.atomic_write(path, payload, mode=0o644)
    except (OSError, util.NetfixError):
        # Кэш не критичен: без него просто проверим в следующий раз.
        pass


def load_found(*, home: str | Path | None = None) -> Found:
    """Прочитать последний сохранённый результат проверки."""

    try:
        raw = json.loads(cache_path(home).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, json.JSONDecodeError):
        return Found()
    if not isinstance(raw, dict):
        return Found()
    return Found(
        zapret_current=_str_or_none(raw.get("zapret_current")),
        zapret_new=_str_or_none(raw.get("zapret_new")),
        tgws_current=_str_or_none(raw.get("tgws_current")),
        tgws_new=_str_or_none(raw.get("tgws_new")),
        checked_at=_str_or_none(raw.get("checked_at")),
    )


def _str_or_none(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def is_due(*, now: datetime | None = None, home: str | Path | None = None) -> bool:
    """Пора ли проверять обновления (раз в сутки)."""

    found = load_found(home=home)
    if not found.checked_at:
        return True
    try:
        last = datetime.fromisoformat(found.checked_at)
    except ValueError:
        return True
    moment = now or datetime.now().astimezone()
    if last.tzinfo is None:
        last = last.replace(tzinfo=moment.tzinfo)
    return moment - last >= CHECK_INTERVAL


# --- Бэкап и восстановление профилей zapret -------------------------------


def profile_dir(install_dir: str | Path | None = None) -> Path:
    return BACKUP_DIR


def save_profile(install_dir: str | Path | None = None) -> dict[str, object]:
    """Сохранить наш конфиг и списки перед обновлением zapret2."""

    base = Path(install_dir) if install_dir else None
    if base is None:
        info = zapret.detect_zapret("zapret2")
        if not info.found or not info.install_dir:
            raise UpdateError("zapret2 не найден")
        base = Path(info.install_dir)

    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    saved: list[str] = []
    for rel in PROFILE_FILES:
        source = base / rel
        if not source.is_file():
            continue
        target = BACKUP_DIR / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        saved.append(rel)

    if "config" not in saved:
        raise UpdateError("не найден конфиг zapret2 — обновление отменено")

    meta = {
        "install_dir": str(base),
        "saved_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "files": saved,
    }
    (BACKUP_DIR / "meta.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return meta


def restore_profile(install_dir: str | Path | None = None) -> dict[str, object]:
    """Вернуть наш конфиг и списки после обновления zapret2."""

    meta_path = BACKUP_DIR / "meta.json"
    if not meta_path.is_file():
        raise UpdateError("нет сохранённого профиля — нечего восстанавливать")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    base = Path(install_dir) if install_dir else Path(str(meta.get("install_dir") or ""))
    if not base.is_dir():
        raise UpdateError(f"каталог zapret2 не найден: {base}")

    restored: list[str] = []
    for rel in meta.get("files") or []:
        source = BACKUP_DIR / str(rel)
        if not source.is_file():
            continue
        target = base / str(rel)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, target)
        restored.append(str(rel))

    return {"install_dir": str(base), "restored": restored}
