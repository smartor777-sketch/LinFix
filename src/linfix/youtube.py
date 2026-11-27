"""YouTube-профили zapret2: ALT9 (hostfakesplit) и ALT11 (fake+multisplit).

На части российских провайдеров общий профиль ``multisplit`` открывает
``google.com``, но не ``youtube.com``/``youtubei.googleapis.com``. Рабочие
варианты приходят из пресета ALT9 и запасного ALT11 из
Flowseal zapret-discord-youtube:

* ALT9 — ``hostfakesplit`` с фейковым хостом ``*.google.com`` и фулингом
  TCP timestamp; фейки видны ТСПУ, но отбрасываются Google;
* ALT11 — подмена всего ClientHello готовым blob-ом ``www.google.com``
  плюс ``multisplit`` с ``seqovl=681``. Это независимый механизм на случай,
  если ТСПУ научится ломать именно ``hostfakesplit``. Для ``googlevideo.com``
  добавляется компаньон ALT9: на этом провайдере чистый ALT11 ломает
  локальный GGC (таймаут), а с компаньоном видео работает.

Профиль включается явно: ``linfix youtube alt9|alt11|off``. Имя профиля
пишется в ``/var/lib/linfix/youtube-profile``; этот файл не трогают ни
переустановка LinFix, ни обновление zapret2, поэтому helper и postinst.deb
могут вернуть именно выбранный профиль.

Список доменов вшит в правило через ``--hostlist-domains``: отдельный файл
в ``ipset`` не нужен, правило сохраняется вместе с конфигом. Маркером правила
служит уникальный аргумент ``hostfakesplit``/``--blob`` — комментарии внутри
``NFQWS2_OPT`` нельзя: при запуске они стали бы аргументами nfqws2.
"""

from __future__ import annotations

from pathlib import Path

from . import applystrategy, util

CONFIG_PATH = Path("/opt/zapret2/config")
STATE_PATH = Path("/var/lib/linfix/youtube-profile")
LEGACY_STATE_PATH = Path("/var/lib/linfix/youtube-enabled")
SERVICE = "zapret2"

PROFILES: tuple[str, ...] = ("alt9", "alt11")
OFF = "off"
DEFAULT_PROFILE = "alt9"

#: Уникальные куски нашего правила — по ним находим и заменяем строку.
RULE_MARKERS: dict[str, str] = {
    "alt9": "hostfakesplit:host=www.google.com",
    # Только имя переменной: совпадает и с ошибочной формой из первой
    # сборки 0.6.21 (``yt_fake_tls@``), и с правильной (``yt_fake_tls:@``).
    "alt11": "yt_fake_tls",
}

#: Домены из list-google.txt пресета ALT9 без не-YouTube сервисов.
DOMAINS: tuple[str, ...] = (
    "youtube.com",
    "youtu.be",
    "youtube-nocookie.com",
    "youtubekids.com",
    "youtube.googleapis.com",
    "youtubei.googleapis.com",
    "youtubeembeddedplayer.googleapis.com",
    "jnn-pa.googleapis.com",
    "googlevideo.com",
    "ytimg.com",
    "ytimg.l.google.com",
    "yt3.ggpht.com",
    "yt4.ggpht.com",
    "yt3.googleusercontent.com",
    "youtube-ui.l.google.com",
    "wide-youtube.l.google.com",
    "yt-video-upload.l.google.com",
    "play.google.com",
    "www.gstatic.com",
)

FAKE_TLS_BLOB = "/opt/zapret2/files/fake/tls_clienthello_www_google_com.bin"

#: Видео-домены идут через ALT9 даже в профиле ALT11 — иначе локальный GGC
#: (rr5---...googlevideo.com -> 188.128.79.16) отвечает по таймауту.
VIDEO_DOMAINS = ("googlevideo.com",)
ALT11_DOMAINS = tuple(domain for domain in DOMAINS if domain not in VIDEO_DOMAINS)


class YouTubeError(util.NetfixError):
    """Не удалось изменить YouTube-профиль."""


class UnknownProfile(YouTubeError):
    """Запрошен неизвестный профиль."""


def validate_profile(profile: str) -> str:
    """Принять ``off`` или один из ALT-профилей."""

    value = str(profile).strip().lower()
    if value == "on":  # обратная совместимость с первой версией 0.6.21
        value = DEFAULT_PROFILE
    if value not in PROFILES + (OFF,):
        raise UnknownProfile(
            f"неизвестный YouTube-профиль: {profile!r}; доступны: "
            + ", ".join(PROFILES + (OFF,))
        )
    return value


def _hostfakesplit_rule(domains: tuple[str, ...]) -> str:
    head = (
        "--filter-tcp=443 --filter-l7=tls --hostlist-domains="
        + ",".join(domains)
        + " --payload=tls_client_hello"
    )
    return (
        head
        + " --lua-desync=hostfakesplit:host=www.google.com:tcp_ts=-1000:"
        + "repeats=4:ip_id=zero --new"
    )


def rule_lines(profile: str) -> list[str]:
    """Правила NFQWS2_OPT для профиля — в порядке применения."""

    profile = validate_profile(profile)
    if profile == OFF:
        raise UnknownProfile("у выключенного профиля нет правила")
    if profile == "alt9":
        return [_hostfakesplit_rule(DOMAINS)]

    # ALT11 дословно повторяет YouTube-правило из general (ALT11).bat
    # Flowseal: tcp_ts=-600000 и seqovl_pattern с тем же blob'ом (681 байт).
    # ':' после имени обязателен: nfqws2 читает имя до ':', затем @<файл>.
    head = (
        "--filter-tcp=443 --filter-l7=tls --hostlist-domains="
        + ",".join(ALT11_DOMAINS)
        + " --payload=tls_client_hello"
    )
    blob = f"--blob=yt_fake_tls:@{FAKE_TLS_BLOB}"
    alt11 = (
        head
        + " "
        + blob
        + " --lua-desync=fake:blob=yt_fake_tls:tcp_ts=-600000:ip_id=zero:repeats=8"
        + " --lua-desync=multisplit:pos=1:seqovl=681:seqovl_pattern=yt_fake_tls:ip_id=zero"
        + " --new"
    )
    # Чистый ALT11 здесь ломает локальный GGC, поэтому видео-домены
    # оставляем на проверенном hostfakesplit.
    return [alt11, _hostfakesplit_rule(VIDEO_DOMAINS)]


def rule_line(profile: str) -> str:
    """Первое правило профиля — для статуса и обратной совместимости."""

    return rule_lines(profile)[0]


def _config_parts(text: str) -> tuple[str, list[str], str]:
    start = text.find(applystrategy.OPT_START)
    if start < 0:
        raise YouTubeError("в конфиге zapret2 нет блока NFQWS2_OPT")
    start += len(applystrategy.OPT_START)
    end = text.find(applystrategy.OPT_END, start)
    if end < 0:
        raise YouTubeError("не найден конец блока NFQWS2_OPT")
    lines = [line for line in text[start:end].splitlines() if line.strip()]
    return text[:start], lines, text[end:]


def is_our_rule(line: str) -> bool:
    """Это наше правило (любого профиля)?"""

    return any(marker in line for marker in RULE_MARKERS.values())


def config_profile(text: str) -> str:
    """Профиль, найденный в конфиге, или ``off``."""

    # ALT11 проверяем первым: в нём есть компаньон ALT9 для видео.
    for profile in ("alt11", "alt9"):
        if RULE_MARKERS[profile] in text:
            return profile
    return OFF


def current_profile(
    *, config_path: str | Path = CONFIG_PATH, state_path: str | Path = STATE_PATH
) -> str:
    """Профиль из маркера; при отсутствии — из конфига (миграция)."""

    for path in (state_path, LEGACY_STATE_PATH):
        try:
            value = Path(path).read_text(encoding="utf-8").strip().lower()
        except OSError:
            continue
        if value == "on":
            return DEFAULT_PROFILE
        if value in PROFILES + (OFF,):
            return value
    try:
        return config_profile(Path(config_path).read_text(encoding="utf-8"))
    except OSError:
        return OFF


def status(
    *,
    config_path: str | Path = CONFIG_PATH,
    state_path: str | Path = STATE_PATH,
    service: str = SERVICE,
) -> dict[str, object]:
    """Состояние маркера, конфига и службы — для CLI и GUI."""

    profile = current_profile(config_path=config_path, state_path=state_path)
    try:
        text = Path(config_path).read_text(encoding="utf-8")
    except OSError:
        text = ""
    found = config_profile(text)
    active = (
        util.run(["systemctl", "is-active", service], check=False, timeout=10).stdout or ""
    ).strip()
    return {
        "ok": True,
        "profile": profile,
        "config_profile": found,
        "enabled": profile != OFF,
        "in_config": found != OFF,
        "needs_restore": profile != OFF and found != profile,
        "service": service,
        "active": active == "active",
        "profiles": list(PROFILES),
        "rule": rule_line(profile) if profile != OFF else None,
        "rules": rule_lines(profile) if profile != OFF else [],
    }


def set_profile(
    profile: str,
    *,
    config_path: str | Path = CONFIG_PATH,
    state_path: str | Path = STATE_PATH,
    service: str = SERVICE,
    restart: bool = True,
) -> dict[str, object]:
    """Включить ALT9/ALT11 или выключить профиль. Возвращает, что изменилось."""

    profile = validate_profile(profile)
    marker = Path(state_path)
    config = Path(config_path)
    try:
        # Явный off пишется в файл: после восстановления старого конфига
        # ensure() не должен вернуть правило, которое пользователь убрал.
        util.atomic_write(marker, profile + "\n", mode=0o644)
        try:
            LEGACY_STATE_PATH.unlink(missing_ok=True)
        except OSError:
            pass  # старый маркер мог быть создан от root — не мешаем работе

        text = config.read_text(encoding="utf-8")
        prefix, lines, suffix = _config_parts(text)
        old = [line for line in lines if is_our_rule(line)]
        lines = [line for line in lines if not is_our_rule(line)]
        new_rules = rule_lines(profile) if profile != OFF else []
        changed = old != new_rules
        lines[0:0] = new_rules
        updated = prefix + "\n".join(lines) + suffix
        if changed:
            util.atomic_write(config, updated)
    except OSError as exc:
        raise YouTubeError(f"не удалось изменить YouTube-профиль: {exc}") from exc

    restarted = False
    if changed and restart:
        restarted = applystrategy.restart(service)
    return {
        "ok": True,
        "profile": profile,
        "enabled": profile != OFF,
        "in_config": bool(new_rules),
        "changed": changed,
        "restarted": restarted,
    }


def set_enabled(
    enabled: bool,
    **kwargs: object,
) -> dict[str, object]:
    """Обратная совместимость: ``on`` = ALT9, ``off`` = выключить."""

    return set_profile(DEFAULT_PROFILE if enabled else OFF, **kwargs)  # type: ignore[arg-type]


def ensure(
    *,
    config_path: str | Path = CONFIG_PATH,
    state_path: str | Path = STATE_PATH,
    service: str = SERVICE,
    restart: bool = False,
) -> dict[str, object]:
    """Вернуть выбранный профиль после обновления/переустановки zapret."""

    profile = current_profile(config_path=config_path, state_path=state_path)
    try:
        text = Path(config_path).read_text(encoding="utf-8")
    except OSError as exc:
        raise YouTubeError(f"не удалось прочитать конфиг zapret2: {exc}") from exc
    if profile == OFF and config_profile(text) == OFF:
        return {"ok": True, "enabled": False, "changed": False, "skipped": True}
    return set_profile(
        profile,
        config_path=config_path,
        state_path=state_path,
        service=service,
        restart=restart,
    )
