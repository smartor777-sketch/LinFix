"""Исправление «чёрной дыры» PMTU.

Симптом: TLS проходит, но передача встаёт после ~16 КБ, потому что интерфейс
заявляет MTU 1500, а провайдер пропускает меньше (у Wi-Fi часто 1492).

Два способа, оба обратимы:

1. ``tcp_mtu_probing=1`` — ядро само находит рабочий MTU (мягкий вариант,
   ничего не ломает, но помогает не всем и не мгновенно);
2. уменьшить MTU интерфейса до найденного значения — действует сразу,
   но меняет настройку сети (сбрасывается при переподключении, если не
   закреплено в NetworkManager).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import util

SYSCTL_KEY = "net.ipv4.tcp_mtu_probing"
DEFAULT_MTU = 1500


@dataclass(frozen=True)
class MtuState:
    interface: str | None
    interface_mtu: int | None
    probing: int | None
    link_mtu: int | None
    link_mtu_source: str | None


def read_state(ip: str | None = None, *, interface: str | None = None) -> MtuState:
    """Собрать текущее состояние: интерфейс, его MTU, probing, MTU маршрута."""

    link_mtu: int | None = None
    source: str | None = None

    if interface is None:
        route_args = (
            ["ip", "-o", "route", "get", ip] if ip else ["ip", "-o", "route", "show", "default"]
        )
        route = util.run(route_args, check=False, timeout=10)
        match = re.search(r"\bdev\s+(\S+)", route.stdout or "")
        if match:
            interface = match.group(1)
        if ip:
            cache_match = re.search(r"\bcache\b.*\bmtu\s+(\d+)", route.stdout or "")
            if cache_match:
                source = cache_match.group(1)

    probing: int | None = None
    try:
        probing = int(Path(f"/proc/sys/{SYSCTL_KEY.replace('.', '/')}").read_text().strip())
    except (OSError, ValueError):
        probing = None

    interface_mtu: int | None = None
    if interface:
        link = util.run(["ip", "-o", "link", "show", interface], check=False, timeout=10)
        mtu_match = re.search(r"\bmtu\s+(\d+)", link.stdout or "")
        if mtu_match:
            interface_mtu = int(mtu_match.group(1))

    return MtuState(
        interface=interface,
        interface_mtu=interface_mtu,
        probing=probing,
        link_mtu=link_mtu,
        link_mtu_source=source,
    )


def enable_probing() -> tuple[int, dict[str, object]]:
    """Включить tcp_mtu_probing=1 (временно, до перезагрузки)."""

    try:
        code, payload = util.run_helper(
            "sysctl.set", ["--key", SYSCTL_KEY, "--value", "1"], timeout=60
        )
    except util.NetfixError as exc:
        return 4, {"ok": False, "error": str(exc)}
    except util.CommandError as exc:
        return 1, {"ok": False, "error": f"sysctl: {exc}"}
    if code != 0:
        return code, payload

    now = read_state()
    return 0, {
        "ok": now.probing == 1,
        "action": "probing",
        "probing": now.probing,
        "value": "1",
    }


def set_interface_mtu(interface: str, mtu: int) -> tuple[int, dict[str, object]]:
    """Уменьшить MTU интерфейса (root через helper)."""

    if not re.fullmatch(r"[A-Za-z0-9_.:-]{1,32}", interface):
        return 2, {"ok": False, "error": f"недопустимое имя интерфейса: {interface}"}
    if not 576 <= mtu <= 1500:
        return 2, {"ok": False, "error": f"MTU вне диапазона 576..1500: {mtu}"}

    try:
        code, payload = util.run_helper(
            "mtu.set", ["--interface", interface, "--mtu", str(mtu)], timeout=60
        )
    except util.NetfixError as exc:
        return 4, {"ok": False, "error": str(exc)}
    except util.CommandError as exc:
        return 1, {"ok": False, "error": f"ip link: {exc}"}
    if code != 0:
        return code, payload

    # Проверяем именно этот интерфейс: read_state() без адреса вернёт
    # интерфейс default-маршрута, и раньше результат терялся.
    now = read_state(interface=interface)
    return 0, {
        "ok": now.interface_mtu == mtu,
        "action": "interface_mtu",
        "interface": interface,
        "mtu": now.interface_mtu,
    }


def describe(state: MtuState) -> str:
    parts: list[str] = []
    if state.interface:
        parts.append(f"{state.interface}: MTU {state.interface_mtu or '?'}")
    parts.append(f"tcp_mtu_probing={state.probing if state.probing is not None else '?'}")
    if state.link_mtu:
        parts.append(f"фактический MTU {state.link_mtu}")
    return ", ".join(parts)
