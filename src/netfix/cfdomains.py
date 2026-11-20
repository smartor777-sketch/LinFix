"""CF-домены TgWsProxy: загрузка, декодирование, merge в hostlist, TLS-тест.

Источник содержит закодированные домены вида ``virkgj.com``. Алгоритм взят из
исходников TgWsProxy (``proxy/config.py``, функция ``_dd``) и проверен на
векторах из SPEC 10.2 и на живом списке от 20.11.2026.
"""

from __future__ import annotations

import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from . import util
from .config import DEFAULT_CF_DOMAINS_URL, DEFAULT_MARKER

#: Проверенный fallback-список (SPEC 10.3). Используется только при недоступном
#: GitHub или сломанном декодировании.
FALLBACK_DOMAINS: tuple[str, ...] = (
    "pclead.co.uk",
    "offshor.co.uk",
    "cakeisalie.co.uk",
    "noskomnadzor.co.uk",
    "lovetrue.co.uk",
    "sorokdva.co.uk",
    "pyatdesyatdva.co.uk",
    "kartoshka.co.uk",
    "sorokodin.co.uk",
    "pyatdesyatodin.co.uk",
    "notelega.co.uk",
    "ebally.co.uk",
    "nebally.co.uk",
    "havegreatday.co.uk",
    "pomogite.co.uk",
    "fixtelega.co.uk",
    "sadnews.co.uk",
    "onedaychamp.co.uk",
    "stopblocking.co.uk",
    "nothingthere.co.uk",
)

DOMAIN_RE = re.compile(r"^[a-z0-9]([a-z0-9.-]*[a-z0-9])?\.[a-z]{2,}$")
USER_AGENT = "netfix-linux/0.1 (+https://github.com/)"


def decode_cf_domain(value: str) -> str:
    """``virkgj.com`` -> ``pclead.co.uk`` (алгоритм TgWsProxy ``_dd``)."""

    if not value.endswith(".com"):
        return value

    core = value[:-4]
    shift = sum(ch.isalpha() for ch in core)
    decoded: list[str] = []
    for ch in core:
        if ch.isalpha():
            base = ord("a") if ch.islower() else ord("A")
            decoded.append(chr((ord(ch) - base - shift) % 26 + base))
        else:
            decoded.append(ch)
    return "".join(decoded) + ".co.uk"


def normalize_domain(value: str) -> str:
    return value.strip().lower().rstrip(".")


def is_valid_domain(value: str) -> bool:
    return bool(DOMAIN_RE.match(value))


def parse_domain_text(text: str) -> list[str]:
    """Разобрать hostlist/список доменов: комментарии и пустые строки пропускаются."""

    text = text.lstrip("\ufeff")
    result: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        result.append(normalize_domain(stripped))
    return result


@dataclass(frozen=True)
class DecodeResult:
    domains: tuple[str, ...]
    total: int
    invalid_count: int
    ok: bool


def decode_encoded_list(lines: list[str] | tuple[str, ...]) -> DecodeResult:
    """Декодировать и провалидировать строки источника.

    Если больше половины строк не проходят валидацию — считаем алгоритм
    сломанным (``ok=False``), вызывающий код обязан взять fallback.
    """

    decoded: list[str] = []
    seen: set[str] = set()
    invalid = 0
    for raw in lines:
        candidate = normalize_domain(decode_cf_domain(normalize_domain(raw)))
        if not is_valid_domain(candidate):
            invalid += 1
            continue
        if candidate not in seen:
            seen.add(candidate)
            decoded.append(candidate)

    total = len(lines)
    ok = total > 0 and invalid <= total / 2
    return DecodeResult(tuple(decoded), total, invalid, ok)


@dataclass(frozen=True)
class DomainSource:
    domains: tuple[str, ...]
    source: str  # "github" | "fallback"
    total: int
    invalid_count: int
    ok: bool
    warnings: tuple[str, ...] = ()


def fallback_source(warnings: tuple[str, ...] = ()) -> DomainSource:
    return DomainSource(
        domains=FALLBACK_DOMAINS,
        source="fallback",
        total=len(FALLBACK_DOMAINS),
        invalid_count=0,
        ok=True,
        warnings=warnings,
    )


def fetch_text(url: str = DEFAULT_CF_DOMAINS_URL, timeout: float = 15.0) -> str:
    # SPEC 15.1: скачивание только по HTTPS.
    if not url.lower().startswith("https://"):
        raise ValueError("скачивание разрешено только по HTTPS")
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - только HTTPS из конфига
        payload = response.read()
    return payload.decode("utf-8-sig")


def domains_from_text(text: str, *, source: str = "github") -> DomainSource:
    lines = parse_domain_text(text)
    decoded = decode_encoded_list(lines)
    if not decoded.ok:
        return DomainSource(
            domains=decoded.domains,
            source=source,
            total=decoded.total,
            invalid_count=decoded.invalid_count,
            ok=False,
            warnings=(
                f"декодирование сломано: {decoded.invalid_count}/{decoded.total} строк невалидны",
            ),
        )
    return DomainSource(
        domains=decoded.domains,
        source=source,
        total=decoded.total,
        invalid_count=decoded.invalid_count,
        ok=True,
    )


def fetch_cf_domains(url: str = DEFAULT_CF_DOMAINS_URL, timeout: float = 15.0) -> DomainSource:
    """Скачать и декодировать список; при любой проблеме — fallback."""

    try:
        text = fetch_text(url, timeout=timeout)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError) as exc:
        return fallback_source((f"не удалось скачать список: {exc}",))

    result = domains_from_text(text)
    if not result.ok or not result.domains:
        warnings = result.warnings or ("пустой список от GitHub",)
        return fallback_source(warnings)
    return result


def load_domain_source(
    url: str = DEFAULT_CF_DOMAINS_URL,
    *,
    timeout: float = 15.0,
    offline: bool = False,
) -> DomainSource:
    if offline:
        return fallback_source(("использован локальный fallback-список (--offline)",))
    return fetch_cf_domains(url, timeout=timeout)


# ---------------------------------------------------------------------------
# Merge в hostlist
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MergeResult:
    text: str
    added: tuple[str, ...]
    present: tuple[str, ...]
    total: tuple[str, ...]
    changed: bool
    newline: str


def detect_newline(text: str) -> str:
    crlf = text.count("\r\n")
    lf = text.count("\n") - crlf
    return "\r\n" if crlf > lf else "\n"


def _normalize_domains(domains: list[str] | tuple[str, ...]) -> list[str]:
    normalized: list[str] = []
    seen: set[str] = set()
    for raw in domains:
        domain = normalize_domain(raw)
        if not domain:
            continue
        if not is_valid_domain(domain):
            raise ValueError(f"некорректный домен: {raw!r}")
        if domain not in seen:
            seen.add(domain)
            normalized.append(domain)
    return normalized


def merge_hostlist(
    text: str,
    domains: list[str] | tuple[str, ...],
    *,
    marker: str = DEFAULT_MARKER,
    today: str | None = None,
) -> MergeResult:
    """Идемпотентно дописать отсутствующие домены в текст hostlist.

    Гарантии:
    - существующие строки и комментарии не меняются;
    - стиль переводов строк (CRLF/LF) сохраняется;
    - при повторном запуске с тем же набором файл не меняется вообще;
    - старые домены никогда не удаляются.
    """

    normalized = _normalize_domains(domains)
    newline = detect_newline(text)

    existing: set[str] = set()
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        existing.add(normalize_domain(stripped))

    present = tuple(domain for domain in normalized if domain in existing)
    missing = [domain for domain in normalized if domain not in existing]

    if not missing:
        return MergeResult(
            text=text,
            added=(),
            present=present,
            total=tuple(normalized),
            changed=False,
            newline=newline,
        )

    lines = text.splitlines(keepends=True)
    if lines and not lines[-1].endswith(("\n", "\r")):
        lines[-1] += newline

    marker_index: int | None = None
    for index, line in enumerate(lines):
        if line.strip().startswith(marker):
            marker_index = index

    insert = [domain + newline for domain in missing]
    if marker_index is not None:
        lines[marker_index + 1 : marker_index + 1] = insert
    else:
        stamp = today or date.today().isoformat()
        lines.append(f"{marker} (updated {stamp}){newline}")
        lines.extend(insert)

    return MergeResult(
        text="".join(lines),
        added=tuple(missing),
        present=present,
        total=tuple(normalized),
        changed=True,
        newline=newline,
    )


def merge_hostlist_file(
    path: str | Path,
    domains: list[str] | tuple[str, ...],
    *,
    marker: str = DEFAULT_MARKER,
    backup_tag: str = "netfix",
) -> tuple[MergeResult, Path | None]:
    """Прочитать hostlist, идемпотентно дополнить, сделать бэкап и записать.

    Бэкап создаётся только если файл реально меняется.
    """

    target = Path(path)
    if target.exists():
        # newline="" отключает универсальную нормализацию: CRLF должен
        # сохраниться, иначе merge перепишет весь файл в LF.
        with target.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            text = handle.read()
        # BOM не должен попасть в hostlist (SPEC 8.3).
        text = text.lstrip("\ufeff")
    else:
        text = ""

    result = merge_hostlist(text, domains, marker=marker)
    if not result.changed:
        return result, None

    backup_path = util.backup_file(target, tag=backup_tag) if target.exists() else None
    # mode=None: atomic_write сохранит права существующего файла,
    # а для нового выставит 0644.
    util.atomic_write(target, result.text)
    return result, backup_path


# ---------------------------------------------------------------------------
# TLS/SNI-проверка
# ---------------------------------------------------------------------------


def sni_ok(host: str, timeout: float = 5.0) -> tuple[bool, str]:
    """Проверить, проходит ли TLS-хендшейк с указанным SNI.

    HTTP-статус не важен: цель — сам факт хендшейка.
    """

    context = ssl.create_default_context()
    started = time.monotonic()
    try:
        with socket.create_connection((host, 443), timeout=timeout) as sock:
            with context.wrap_socket(sock, server_hostname=host) as tls:
                version = tls.version() or "TLS"
                elapsed = time.monotonic() - started
                return True, f"{version} ({elapsed:.2f} с)"
    except (socket.timeout, TimeoutError):
        return False, "timeout (SNI заблокирован или zapret не применён)"
    except ssl.SSLError as exc:
        return False, f"tls error: {getattr(exc, 'reason', None) or exc}"
    except OSError as exc:
        return False, f"tcp error: {exc}"


def test_domains(
    domains: list[str] | tuple[str, ...] | None = None,
    *,
    limit: int | None = 3,
    timeout: float = 5.0,
    dc_prefixes: tuple[str, ...] = ("kws2", "kws4"),
) -> list[tuple[str, bool, str]]:
    """Проверить TLS для ``kws2.<domain>`` и ``kws4.<domain>``.

    ``limit`` ограничивает число доменов (по умолчанию 3), ``None`` — все.
    """

    selected = list(domains or FALLBACK_DOMAINS)
    if limit is not None:
        selected = selected[:limit]

    results: list[tuple[str, bool, str]] = []
    for domain in selected:
        for prefix in dc_prefixes:
            host = f"{prefix}.{domain}"
            ok, detail = sni_ok(host, timeout=timeout)
            results.append((host, ok, detail))
    return results
