#!/usr/bin/env python3
"""Раскодировать список CF-доменов TgWsProxy.

В репозитории Flowseal/tg-ws-proxy домены лежат не открытым текстом, а
«кодами»: `.github/cfproxy-domains.txt` и `_CFPROXY_ENC` в `proxy/config.py`
содержат строки вида `virkgj.com`. Клиент TgWsProxy раскодирует их функцией
`_dd()`; LinFix делает то же самое в `linfix.cfdomains.decode_cf_domain`.

## Алгоритм

Для строки, оканчивающейся на `.com`:

1. отрезать `.com`;
2. посчитать число букв в остатке — это ключ `n`;
3. каждую букву сдвинуть назад по алфавиту на `n` (с заворотом через `z`);
4. добавить `.co.uk`.

Пример `virkgj.com`:

    virkgj   букв 6
    v -6 -> p      i -6 -> c      r -6 -> l
    k -6 -> e      g -6 -> a      j -6 -> d
    результат: pclead.co.uk

Сдвиг зависит от длины имени, поэтому один универсальный сдвиг не подходит —
именно это и делает список «неочевидным» для простых скраперов.

## Использование

    # локальный файл с кодами (например, сохранённый из upstream)
    scripts/decode-cf-domains.py /path/to/cfproxy-domains.txt

    # скачать актуальный список с GitHub и раскодировать
    scripts/decode-cf-domains.py --upstream

    # одна строка
    scripts/decode-cf-domains.py virkgj.com

    # машинный вывод
    scripts/decode-cf-domains.py --upstream --json
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from pathlib import Path

UPSTREAM_URL = (
    "https://raw.githubusercontent.com/Flowseal/tg-ws-proxy/main"
    "/.github/cfproxy-domains.txt"
)


def decode(value: str) -> str:
    """Точная копия `_dd()` из TgWsProxy и `decode_cf_domain` из LinFix."""

    domain = value.strip().lower().rstrip(".")
    if not domain.endswith(".com"):
        return domain
    core = domain[:-4]
    shift = sum(ch.isalpha() for ch in core)
    decoded = []
    for ch in core:
        if ch.isalpha():
            base = ord("a") if ch.islower() else ord("A")
            decoded.append(chr((ord(ch) - base - shift) % 26 + base))
        else:
            decoded.append(ch)
    return "".join(decoded) + ".co.uk"


def fetch_upstream(timeout: float = 15.0) -> list[str]:
    request = urllib.request.Request(UPSTREAM_URL, headers={"User-Agent": "linfix"})
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
        text = response.read().decode("utf-8-sig")
    return [
        line.strip()
        for line in text.splitlines()
        if line.strip() and not line.startswith("#")
    ]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("items", nargs="*", help="коды или путь к файлу со списком")
    parser.add_argument(
        "--upstream", action="store_true", help="взять актуальный список с GitHub"
    )
    parser.add_argument("--json", action="store_true", help="машинный вывод")
    args = parser.parse_args()

    raw: list[str] = []
    if args.upstream:
        try:
            raw.extend(fetch_upstream())
        except Exception as exc:  # noqa: BLE001
            print(f"не удалось скачать список: {exc}", file=sys.stderr)
            return 1
    for item in args.items:
        path = Path(item)
        if path.is_file():
            raw.extend(
                line.strip()
                for line in path.read_text(encoding="utf-8", errors="replace").splitlines()
                if line.strip() and not line.startswith("#")
            )
        else:
            raw.append(item)

    if not raw:
        parser.error("нечего декодировать: передайте коды, файл или --upstream")

    result = [{"source": item, "domain": decode(item)} for item in raw]
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        width = max(len(row["source"]) for row in result)
        for row in result:
            print(f"{row['source']:<{width}}  ->  {row['domain']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
