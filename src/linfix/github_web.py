"""Данные о релизах GitHub без REST API.

Анонимный ``api.github.com`` даёт всего 60 запросов в час на IP. Для
проверки обновлений этого мало: достаточно несколько раз открыть «Статус»
или нажать «Проверить обновления», и лимит исчерпан.

Публичные страницы GitHub и Atom-лента релизов не входят в этот лимит.
Модуль вытаскивает оттуда последний тег и файлы релиза (имя, URL, sha256),
чтобы LinFix мог проверять обновления так часто, как нужно.
"""

from __future__ import annotations

import html
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


class GitHubWebError(Exception):
    """Не удалось получить данные со страницы GitHub."""


def _open(request: urllib.request.Request, *, timeout: float, attempts: int = 3):
    """``urlopen`` с повторами на таймаутах/обрывах.

    GitHub периодически отвечает медленно (особенно через нестабильный
    канал). HTTP-ошибки не повторяем: 404/403 сами по себе информативны.
    """

    last_error: Exception | None = None
    for attempt in range(1, max(1, attempts) + 1):
        try:
            return urllib.request.urlopen(request, timeout=timeout)  # noqa: S310
        except urllib.error.HTTPError:
            raise
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt < attempts:
                time.sleep(min(attempt, 3))
    raise GitHubWebError(f"не удалось запросить GitHub: {last_error}") from last_error


@dataclass(frozen=True)
class WebAsset:
    """Файл релиза, найденный на странице GitHub."""

    name: str
    url: str
    sha256: str = ""
    size: int = 0


_SIZE_RE = re.compile(r">\s*([\d.]+)\s*(Bytes|KB|MB|GB)\s*<", re.IGNORECASE)
_SIZE_FACTORS = {"bytes": 1, "kb": 1024, "mb": 1024**2, "gb": 1024**3}


def _parse_size(text: str) -> int:
    """Размер файла из HTML («54.7 MB») в байтах. 0 — не нашли."""

    match = _SIZE_RE.search(text)
    if not match:
        return 0
    try:
        value = float(match.group(1))
    except ValueError:
        return 0
    return int(value * _SIZE_FACTORS[match.group(2).lower()])


def _fetch_text(url: str, *, timeout: float = 20.0) -> str:
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "linfix",
            "Accept": "text/html,application/xhtml+xml,application/atom+xml",
        },
    )
    try:
        with _open(request, timeout=timeout) as response:
            payload = response.read()
    except urllib.error.HTTPError as exc:
        raise GitHubWebError(f"GitHub ответил ошибкой {exc.code}") from exc
    return payload.decode("utf-8", "replace")


def latest_tag(repo: str, *, timeout: float = 20.0) -> str:
    """Последний тег релиза через ``/releases/latest``.

    ``/releases/latest`` отдаёт 302 на ``/releases/tag/<tag>``. Это обычная
    HTML-страница, а не API, поэтому лимит 60 запросов в час не тратится.
    """

    url = f"https://github.com/{repo}/releases/latest"
    request = urllib.request.Request(url, headers={"User-Agent": "linfix"})
    final_url = ""
    try:
        # urllib следует за 302 сам; geturl() — уже конечный адрес.
        response = _open(request, timeout=timeout)
        try:
            final_url = response.geturl()
        finally:
            response.close()
    except urllib.error.HTTPError as exc:
        # 404 — у репозитория может не быть «latest», но быть релизы.
        # Остальные коды (403, 500) — реальная ошибка.
        if exc.code != 404:
            raise GitHubWebError(f"GitHub ответил ошибкой {exc.code}") from exc

    match = re.search(r"/releases/tag/([^/?#]+)", final_url)
    if match:
        return urllib.parse.unquote(match.group(1))

    # Репозиторий без релизов: пробуем Atom-ленту. Она отдаёт теги даже
    # когда /releases/latest не редиректит на конкретный релиз.
    text = _fetch_text(f"https://github.com/{repo}/releases.atom", timeout=timeout)
    match = re.search(
        r'<entry>.*?<link[^>]+href="[^"]*/releases/tag/([^"]+)"',
        text,
        re.DOTALL,
    )
    if not match:
        raise GitHubWebError("на странице релизов не найден тег")
    return urllib.parse.unquote(html.unescape(match.group(1)))


def release_assets(
    repo: str, tag: str, *, timeout: float = 20.0
) -> list[WebAsset]:
    """Файлы релиза и их sha256 со страницы ``expanded_assets``.

    GitHub показывает список файлов отдельной HTML-страницей. Каждый файл
    идёт ссылкой ``/releases/download/<tag>/<name>``, а рядом — его digest
    ``sha256:...``. Этого достаточно, чтобы скачать и проверить файл без
    обращения к API.
    """

    quoted_tag = urllib.parse.quote(tag, safe="")
    url = f"https://github.com/{repo}/releases/expanded_assets/{quoted_tag}"
    text = _fetch_text(url, timeout=timeout)

    pattern = re.compile(
        r'href="(/' + re.escape(repo) + r'/releases/download/[^"]+)"'
    )
    matches = list(pattern.finditer(text))
    assets: list[WebAsset] = []
    seen: set[str] = set()

    for index, match in enumerate(matches):
        href = html.unescape(match.group(1))
        name = urllib.parse.unquote(href.rsplit("/", 1)[-1])
        if not name or name in seen:
            continue
        seen.add(name)

        # Digest относится к текущему файлу: ищем его до следующей ссылки.
        next_start = (
            matches[index + 1].start() if index + 1 < len(matches) else len(text)
        )
        chunk = text[match.end() : next_start]
        digest_match = re.search(r"sha256:([0-9a-fA-F]{64})", chunk)
        sha256 = digest_match.group(1).lower() if digest_match else ""

        assets.append(
            WebAsset(
                name=name,
                url=f"https://github.com{href}",
                sha256=sha256,
                size=_parse_size(chunk),
            )
        )

    if not assets:
        raise GitHubWebError(f"на странице релиза {tag} не найдено файлов")
    return assets
