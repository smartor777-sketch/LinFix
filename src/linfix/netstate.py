"""Живое состояние IPv6-заглушек и активного VPN.

Одно место для проверок, которые нужны и guard, и doctor: иначе они
расходятся в трактовке одного и того же состояния системы.
"""

from __future__ import annotations

from . import util

#: Маршруты-заглушки IPv6, которые ставят VPN-клиенты с kill-switch.
IPV6_BLACKHOLE_ROUTES = ("::/1", "8000::/1", "2000::/4", "3000::/4", "fc00::/7")

#: Имена процессов VPN-клиентов: если такой жив, маршруты ему нужны.
VPN_PROCESSES = ("qeli", "amnezia-linux", "wg-quick", "openvpn")

#: С этих префиксов начинаются туннельные интерфейсы VPN.
VPN_INTERFACE_PREFIXES = ("tun", "vpn", "wg", "qeli")


def ipv6_blackhole_routes() -> list[str]:
    """Вернуть список маршрутов-заглушек IPv6 (пусто — чисто)."""

    completed = util.run(["ip", "-6", "route", "show"], check=False, timeout=10)
    stdout = getattr(completed, "stdout", "") or ""
    if not isinstance(stdout, str):
        # В тестах util.run может быть заглушён произвольным объектом.
        return []
    return [
        line.strip()
        for line in stdout.splitlines()
        if line.strip().startswith("blackhole")
    ]


def vpn_interfaces() -> list[str]:
    """Туннельные интерфейсы и процессы VPN — признак активного соединения."""

    names: list[str] = []
    link = util.run(["ip", "-brief", "link"], check=False, timeout=10)
    stdout = getattr(link, "stdout", "") or ""
    if isinstance(stdout, str):
        for line in stdout.splitlines():
            parts = line.split()
            if parts and parts[0].startswith(VPN_INTERFACE_PREFIXES):
                names.append(parts[0])
    for client in VPN_PROCESSES:
        completed = util.run(["pgrep", "-x", client], check=False, timeout=5)
        if getattr(completed, "returncode", 1) == 0:
            names.append(client)
    return names


def clear_ipv6_blackholes() -> tuple[int, list[str]]:
    """Удалить маршруты-заглушки. Вернуть (сколько удалено, что осталось).

    Вызывать только при неактивном VPN: иначе маршруты нужны клиенту.
    Требует root (через helper или pkexec).
    """

    removed = 0
    for route in IPV6_BLACKHOLE_ROUTES:
        completed = util.run(
            ["ip", "-6", "route", "del", "blackhole", route], check=False, timeout=10
        )
        if getattr(completed, "returncode", 1) == 0:
            removed += 1
    return removed, ipv6_blackhole_routes()
