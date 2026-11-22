"""Проверка произвольного сайта: почему он не открывается.

Отделяет разные причины друг от друга, чтобы не гадать:

- ``dns``       — имя не резолвится;
- ``ip_block``  — TCP не устанавливается вовсе (блокировка по IP);
- ``tls_ip``    — TCP есть, но TLS-хендшейк режется при любом SNI
                  (блокировка TLS на этом IP, zapret не поможет);
- ``sni``       — TLS проходит с «чужим» SNI, но не с настоящим:
                  режут именно имя, и здесь помогает zapret;
- ``ok``        — сайт открывается;
- ``slow``      — открывается, но медленно (сервер или канал);
- ``http_only`` — HTTPS режется, HTTP открывается.

Каждая проверка короткая и не меняет систему.
"""

from __future__ import annotations

import re
import socket
import ssl
import time
import urllib.error
import urllib.request
from pathlib import Path
from dataclasses import dataclass, field

from . import util as _util

util_which = _util.which
util_run = _util.run

DEFAULT_TIMEOUT = 8.0

#: Публичный домен для проверки «TLS на этом IP с чужим SNI».
_PROBE_SNI = "www.cloudflare.com"


@dataclass
class SiteCheck:
    host: str
    status: str
    summary: str
    details: list[str] = field(default_factory=list)
    ip: str | None = None
    advice: str = ""
    #: Для статуса pmtu: рабочий MTU, который можно применить.
    suggested_mtu: int | None = None


def _resolve(host: str, timeout: float) -> tuple[str | None, str]:
    started = time.monotonic()
    try:
        infos = socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        return None, f"DNS: {exc}"
    elapsed = time.monotonic() - started
    # предпочитаем IPv4: на многих провайдерах IPv6 «висит»
    v4 = [info[4][0] for info in infos if info[0] == socket.AF_INET]
    v6 = [info[4][0] for info in infos if info[0] == socket.AF_INET6]
    addr = v4[0] if v4 else (v6[0] if v6 else None)
    parts = [f"DNS за {elapsed * 1000:.0f} мс"]
    if v4:
        parts.append(f"IPv4 {v4[0]}")
    if v6:
        parts.append(f"IPv6 {v6[0]}")
    return addr, ", ".join(parts)


def _tcp_ok(ip: str, port: int, timeout: float) -> tuple[bool, float, str]:
    started = time.monotonic()
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True, time.monotonic() - started, ""
    except (socket.timeout, TimeoutError):
        return False, time.monotonic() - started, "timeout"
    except OSError as exc:
        return False, time.monotonic() - started, str(exc)


def _tls_handshake(ip: str, sni: str | None, timeout: float) -> tuple[bool, float, str]:
    context = ssl.create_default_context()
    context.check_hostname = bool(sni)
    if not sni:
        context.verify_mode = ssl.CERT_NONE
    started = time.monotonic()
    try:
        with socket.create_connection((ip, 443), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=sni) as tls:
                return True, time.monotonic() - started, tls.version() or "TLS"
    except (socket.timeout, TimeoutError):
        return False, time.monotonic() - started, "timeout"
    except ssl.SSLError as exc:
        return False, time.monotonic() - started, getattr(exc, "reason", None) or str(exc)
    except OSError as exc:
        return False, time.monotonic() - started, str(exc)


def _http_ok(host: str, timeout: float) -> tuple[bool, int, float]:
    started = time.monotonic()
    request = urllib.request.Request(
        f"http://{host}/", headers={"User-Agent": "linfix/check"}
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            response.read(2048)
            return True, response.status, time.monotonic() - started
    except Exception:  # noqa: BLE001 — статус не важен, проверяем доступность
        return False, 0, time.monotonic() - started


def _https_fetch(host: str, timeout: float, *, limit: int = 65536) -> tuple[int, str, bool]:
    """Скачать начало страницы по HTTPS.

    Возвращает (байт получено, причина остановки, дочитали ли до limit).
    Именно этот шаг ловит «хендшейк прошёл, а данные застряли» — случай,
    когда TLS-тест говорит «всё хорошо», а сайт не открывается.
    """

    request = urllib.request.Request(
        f"https://{host}/", headers={"User-Agent": "linfix/check"}
    )
    received = 0
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            deadline = time.monotonic() + timeout
            while received < limit:
                if time.monotonic() > deadline:
                    return received, "загрузка не завершилась за отведённое время", False
                chunk = response.read(8192)
                if not chunk:
                    return received, "загрузка завершена", True
                received += len(chunk)
            return received, "прочитан лимит", True
    except (socket.timeout, TimeoutError):
        return received, "таймаут посреди загрузки (данные застряли)", False
    except urllib.error.HTTPError as exc:
        # HTTP-ошибка — это всё равно работающий сайт.
        return received, f"HTTP {exc.code}", True
    except Exception as exc:  # noqa: BLE001
        return received, f"ошибка: {exc}", False


def hostlist_paths() -> list[Path]:
    """Где искать пользовательский hostlist zapret."""

    return [
        Path("/opt/zapret2/ipset/zapret-hosts-user.txt"),
        Path("/opt/zapret/ipset/zapret-hosts-user.txt"),
    ]


def _in_zapret_hostlist(host: str, paths: list[Path] | None = None) -> bool | None:
    """Есть ли домен в списках zapret (тогда на него действует desync)."""

    paths = paths if paths is not None else hostlist_paths()
    checked = False
    for path in paths:
        if not path.is_file():
            continue
        checked = True
        try:
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                item = line.strip().lower()
                if not item or item.startswith("#"):
                    continue
                if host == item or host.endswith("." + item):
                    return True
        except OSError:
            continue
    return False if checked else None


def _interface_mtu(ip: str) -> int | None:
    """MTU интерфейса, через который идёт маршрут до адреса."""

    route = util_run(["ip", "-o", "route", "get", ip], timeout=10, check=False)
    match = re.search(r"\bdev\s+(\S+)", route.stdout or "")
    if not match:
        return None
    link = util_run(["ip", "-o", "link", "show", match.group(1)], timeout=10, check=False)
    mtu_match = re.search(r"\bmtu\s+(\d+)", link.stdout or "")
    return int(mtu_match.group(1)) if mtu_match else None


def _path_mtu(ip: str, timeout: float = 2.0) -> int | None:
    """Максимальный размер ICMP-пакета, который проходит до адреса (DF).

    Разница с MTU интерфейса означает «дыру» в PMTU: большие пакеты
    молча теряются, и TCP-передача встаёт после первых килобайт.
    """

    if util_which("ping") is None:
        return None
    low, high = 1200, 1472
    best: int | None = None
    while low <= high:
        mid = (low + high) // 2
        completed = util_run(
            ["ping", "-4", "-M", "do", "-s", str(mid), "-c", "1", "-W", str(int(timeout)), ip],
            timeout=timeout + 2,
            check=False,
        )
        if completed.returncode == 0:
            best = mid
            low = mid + 1
        else:
            high = mid - 1
    return (best + 28) if best else None


def check_site(host: str, *, timeout: float = DEFAULT_TIMEOUT) -> SiteCheck:
    """Определить, почему сайт не открывается (или открывается плохо)."""

    host = host.strip().lower()
    for prefix in ("https://", "http://"):
        if host.startswith(prefix):
            host = host[len(prefix) :]
    host = host.split("/")[0]
    if not host:
        return SiteCheck(host, "error", "пустое имя сайта")

    ip, dns_detail = _resolve(host, timeout)
    if ip is None:
        return SiteCheck(
            host,
            "dns",
            "Имя не резолвится",
            [dns_detail],
            advice="Проверьте DNS (systemd-resolved/роутер) или опечатку в домене.",
        )

    details = [dns_detail]

    tcp_ok, tcp_time, tcp_err = _tcp_ok(ip, 443, timeout)
    if not tcp_ok:
        tcp80_ok, _, _ = _tcp_ok(ip, 80, timeout)
        details.append(f"TCP 443: {tcp_err} за {tcp_time:.1f} с")
        details.append(f"TCP 80: {'открыт' if tcp80_ok else 'закрыт'}")
        if tcp80_ok:
            return SiteCheck(
                host,
                "ip_block",
                "Порт 443 закрыт, порт 80 открыт — блокировка по IP/порту",
                details,
                ip=ip,
                advice=(
                    "zapret здесь не поможет: он работает с SNI, а не с портом. "
                    "Ищите другой IP/зеркало или используйте VPN."
                ),
            )
        return SiteCheck(
            host,
            "ip_block",
            "TCP-соединение не устанавливается",
            details,
            ip=ip,
            advice="Похоже на блокировку по IP. Проверьте доступность из другой сети.",
        )

    details.append(f"TCP 443: открыт за {tcp_time * 1000:.0f} мс")

    real_ok, real_time, real_info = _tls_handshake(ip, host, timeout)
    if real_ok:
        details.append(f"TLS с SNI {host}: {real_info} за {real_time:.2f} с")
        # Хендшейк прошёл — проверяем, идут ли данные. Иначе можно решить,
        # что сайт в порядке, хотя страница не грузится (случай rutracker).
        received, reason, complete = _https_fetch(host, timeout)
        details.append(f"Загрузка по HTTPS: {received} байт, {reason}")
        if not complete:
            local_mtu = _interface_mtu(ip) if ip else None
            mtu = _path_mtu(ip)
            if mtu is None:
                details.append("ICMP-проверка MTU недоступна")
            else:
                if mtu is not None and local_mtu is not None and mtu >= local_mtu:
                    details.append(
                        f"PMTU согласован: {mtu} байт (локальный MTU {local_mtu}) — не причина"
                    )
                else:
                    details.append(
                        f"PMTU до сервера: {mtu} байт (локальный MTU {local_mtu or 1500})"
                    )
            # Сравниваем с ФАКТИЧЕСКИМ MTU интерфейса, а не с 1500:
            # после первого «Починить MTU» он уже 1492, и старом условие
            # продолжало ругаться на PMTU, хотя всё согласовано.
            if mtu is not None and local_mtu is not None and mtu < local_mtu:
                return SiteCheck(
                    host,
                    "pmtu",
                    f"Данные застревают: MTU до сервера {mtu}, локальный {local_mtu}",
                    details,
                    ip=ip,
                    advice=(
                        "TLS проходит, но крупные пакеты теряются — «чёрная дыра» PMTU. "
                        "Нажмите «Починить MTU»: включить tcp_mtu_probing "
                        "или уменьшить MTU интерфейса."
                    ),
                    suggested_mtu=mtu,
                )
            in_lists = _in_zapret_hostlist(host)
            if in_lists:
                return SiteCheck(
                    host,
                    "desync",
                    "Данные застревают при включённом desync для этого домена",
                    details,
                    ip=ip,
                    advice=(
                        "Домен есть в hostlist zapret, и передача ломается именно на нём. "
                        "Варианты: (1) исключить домен из hostlist — кнопка ниже или "
                        "«Списки»; (2) сменить стратегию desync в конфиге zapret "
                        "(это НЕ MTU и не режим TgWsProxy); (3) оставить как есть, если "
                        "важнее другие сайты. Остановка zapret на минуту покажет, "
                        "виноват ли он вообще."
                    ),
                )
            return SiteCheck(
                host,
                "slow",
                "TLS проходит, но данные почти не идут",
                details,
                ip=ip,
                advice=(
                    "Домена нет в списках zapret, значит desync ни при чём. "
                    "Скорее всего сервер перегружен или канал узкий."
                ),
            )
        if real_time > 3.0 or not complete:
            return SiteCheck(
                host,
                "slow",
                f"Сайт открывается, но медленно ({real_time:.1f} с на TLS)",
                details,
                ip=ip,
                advice="Блокировки нет — сервер перегружен или плохой маршрут.",
            )
        return SiteCheck(host, "ok", "Сайт открывается", details, ip=ip)

    details.append(f"TLS с SNI {host}: {real_info} за {real_time:.1f} с")

    # Настоящий SNI не прошёл. Проверяем два варианта:
    # 1) TLS на этот IP режется вообще (любой SNI) → zapret бессилен;
    # 2) режут именно имя → zapret поможет.
    foreign_ok, _, foreign_info = _tls_handshake(ip, _PROBE_SNI, timeout)
    details.append(
        f"TLS с чужим SNI ({_PROBE_SNI}): "
        + ("прошёл" if foreign_ok else f"не прошёл ({foreign_info})")
    )

    if not foreign_ok:
        http_ok, status, http_time = _http_ok(host, timeout)
        if http_ok:
            details.append(f"HTTP: {status} за {http_time:.2f} с")
            return SiteCheck(
                host,
                "http_only",
                "HTTPS режется полностью, HTTP работает",
                details,
                ip=ip,
                advice=(
                    "zapret не обходит блокировку TLS на этом IP. "
                    "Работает только http://-версия сайта (если он её поддерживает)."
                ),
            )
        return SiteCheck(
            host,
            "tls_ip",
            "TLS режется при любом SNI — блокировка на уровне IP",
            details,
            ip=ip,
            advice=(
                "zapret не поможет: он маскирует SNI, а здесь режут сам TLS-хендшейк. "
                "Нужен VPN или другое зеркало."
            ),
        )

    details.append("Вывод: режут именно SNI этого домена")
    in_lists = _in_zapret_hostlist(host)
    if in_lists:
        # Домен уже обходится zapret, но TLS всё равно не проходит — значит
        # текущая стратегия ему не подходит. Совет «добавьте в hostlist» тут
        # вводит в заблуждение (так было с rutracker.org).
        details.append(
            f"Домен уже в hostlist zapret, но TLS с ним не проходит: "
            "текущая стратегия (desync) для него не срабатывает"
        )
        return SiteCheck(
            host,
            "desync",
            "Домен в hostlist, но TLS не проходит — стратегия не срабатывает",
            details,
            ip=ip,
            advice=(
                "Домен уже добавлен, поэтому добавлять повторно не нужно. "
                "Попробуйте другую стратегию: «Диагностика» → «Подобрать "
                "стратегию» (домен уже входит в список подбора)."
            ),
        )
    return SiteCheck(
        host,
        "sni",
        "Блокировка по SNI — это случай для zapret",
        details,
        ip=ip,
        advice=(
            f"Добавьте {host} в hostlist zapret и перезапустите службу."
        ),
    )
