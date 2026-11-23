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
#: У zapret2 бинарники есть ТОЛЬКО в релизах (в git-архиве лишь readme.txt),
#: поэтому сравниваем теги релизов, а не коммиты master.
ZAPRET_RELEASE_API = f"https://api.github.com/repos/{ZAPRET_REPO}/releases/latest"

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

    zapret_tag: str | None = None
    zapret_asset: str | None = None
    zapret_asset_url: str | None = None
    zapret_asset_sha256: str | None = None
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

    zapret_tag: str | None = None
    zapret_asset: str | None = None
    zapret_url: str | None = None
    zapret_sha: str | None = None
    try:
        release = _github_json(ZAPRET_RELEASE_API, timeout=timeout)
        if isinstance(release, dict):
            tag = release.get("tag_name")
            if isinstance(tag, str):
                zapret_tag = tag
            assets = release.get("assets")
            if isinstance(assets, list):
                for asset in assets:
                    if not isinstance(asset, dict):
                        continue
                    name = str(asset.get("name") or "")
                    # Нужен обычный релизный архив: «zapret2-vX.Y.Z.tar.gz».
                    # openwrt-embedded, .zip и sha256sum.txt не подходят.
                    if not (name.startswith("zapret2-v") and name.endswith(".tar.gz")):
                        continue
                    if "openwrt" in name:
                        continue
                    dig = asset.get("digest")
                    if isinstance(dig, str) and dig.startswith("sha256:"):
                        zapret_sha = dig.split(":", 1)[1].lower()
                    zapret_url = str(asset.get("browser_download_url") or "")
                    zapret_asset = name
                    break
    except UpdateError:
        zapret_tag = None

    tgws_tag: str | None = None
    try:
        tgws_tag = tgws.fetch_release(timeout=timeout).tag
    except tgws.TgwsError:
        tgws_tag = None

    if not zapret_tag and not tgws_tag:
        raise UpdateError("не удалось получить сведения об обновлениях")
    return Remote(
        zapret_tag=zapret_tag,
        zapret_asset=zapret_asset,
        zapret_asset_url=zapret_url,
        zapret_asset_sha256=zapret_sha,
        tgws_tag=tgws_tag,
    )


#: Файл, куда LinFix записывает установленную версию zapret2.
ZAPRET_VERSION_FILE = Path("/var/lib/linfix/zapret-version")


def installed_zapret_version() -> str | None:
    """Версия установленного zapret2.

    Берём из файла, который пишет установщик LinFix. Если файла нет
    (zapret ставился вручную) — спрашиваем сам бинарник: он печатает
    ``github version v1.0.6 (...)``.
    """

    try:
        recorded = ZAPRET_VERSION_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        recorded = ""
    if recorded:
        return recorded

    info = zapret.detect_zapret("zapret2")
    if not info.found or not info.install_dir:
        return None
    binary = Path(info.install_dir) / "nfq2" / "nfqws2"
    if not binary.exists():
        return None
    completed = util.run([str(binary), "--version"], check=False, timeout=10)
    text = (completed.stdout or "").strip()
    # «github version v1.0.6 (221d7dff...) lua_compat_ver 6»
    for token in text.split():
        if token.startswith("v") and token[1:2].isdigit():
            return token
    return None


def _version_tuple(tag: str) -> tuple[int, ...]:
    parts: list[int] = []
    for chunk in tag.lstrip("v").split("."):
        digits = "".join(ch for ch in chunk if ch.isdigit())
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _is_newer(candidate: str | None, current: str | None) -> bool:
    if not candidate:
        return False
    if not current:
        return True
    return _version_tuple(candidate) > _version_tuple(current)


def compare(remote: Remote, *, cfg_state: state.State | None = None) -> Found:
    current_state = cfg_state or state.load_state()
    zapret_current = installed_zapret_version()
    tgws_current = current_state.tgws_version
    if tgws_current is None and tgws.is_installed():
        tgws_current = "неизвестно"

    return Found(
        zapret_current=zapret_current,
        zapret_new=remote.zapret_tag if _is_newer(remote.zapret_tag, zapret_current) else None,
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
