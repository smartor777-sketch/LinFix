"""Диагностика: набор проверок и отчёт PASS/WARN/FAIL."""

from __future__ import annotations

import json
import platform
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from . import cfdomains, guard, state, util, zapret
from .cfdomains import FALLBACK_DOMAINS
from .config import Config

PASS = "pass"
WARN = "warn"
FAIL = "fail"


@dataclass(frozen=True)
class Check:
    id: str
    title: str
    status: str
    details: str
    hint: str = ""


@dataclass(frozen=True)
class Report:
    checks: tuple[Check, ...]

    @property
    def ok(self) -> bool:
        return all(check.status != FAIL for check in self.checks)

    def to_json(self) -> str:
        payload = {
            "ok": self.ok,
            "checks": [asdict(check) for check in self.checks],
        }
        return json.dumps(payload, ensure_ascii=False, indent=2)


def _os_check() -> Check:
    pretty = util.os_pretty_name()
    missing = [tool for tool in ("systemctl", "nft", "ss") if util.which(tool) is None]
    if missing:
        return Check(
            "os",
            "Система",
            FAIL,
            f"{pretty}; отсутствуют: {', '.join(missing)}",
            "Установите пакеты systemd, nftables и iproute2",
        )
    lowered = pretty.lower()
    if not any(name in lowered for name in ("mint", "ubuntu", "debian")):
        return Check(
            "os",
            "Система",
            WARN,
            f"{pretty}, Python {platform.python_version()} — не Mint/Ubuntu",
            "Дистрибутив не целевой; часть проверок может отличаться",
        )
    return Check("os", "Система", PASS, f"{pretty}, Python {platform.python_version()}")


def _nfqueue_check() -> Check:
    try:
        modules = Path("/proc/modules").read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return Check("nfqueue", "Модуль nfnetlink_queue", WARN, f"не удалось прочитать /proc/modules: {exc}")
    if "nfnetlink_queue" in modules:
        return Check("nfqueue", "Модуль nfnetlink_queue", PASS, "загружен")
    completed = util.run(["modprobe", "-n", "nfnetlink_queue"], check=False, timeout=10)
    if completed.returncode == 0:
        return Check(
            "nfqueue",
            "Модуль nfnetlink_queue",
            WARN,
            "не загружен, но доступен",
            "sudo modprobe nfnetlink_queue",
        )
    return Check("nfqueue", "Модуль nfnetlink_queue", FAIL, "недоступен", "Проверьте ядро и модули")


def _zapret_checks(info: zapret.ZapretInfo) -> list[Check]:
    checks: list[Check] = []
    if not info.found:
        checks.append(
            Check(
                "zapret.service",
                "Служба zapret",
                FAIL,
                "не найдена",
                "Установите zapret: git clone https://github.com/bol-van/zapret && cd zapret && sudo ./install_easy.sh",
            )
        )
        return checks

    state = "active" if info.active else "inactive"
    enabled = info.enabled_state or ("enabled" if info.enabled else "disabled")
    if info.active and info.enabled:
        checks.append(Check("zapret.service", "Служба zapret", PASS, f"{info.service}: active ({enabled})"))
    elif info.active:
        checks.append(
            Check(
                "zapret.service",
                "Служба zapret",
                WARN,
                f"{info.service}: active ({enabled})",
                f"sudo systemctl enable {info.service}",
            )
        )
    else:
        checks.append(
            Check(
                "zapret.service",
                "Служба zapret",
                FAIL,
                f"{info.service}: {state} ({enabled})",
                f"sudo systemctl start {info.service}",
            )
        )

    if info.pid is not None:
        checks.append(
            Check(
                "zapret.process",
                "Процесс nfqws",
                PASS,
                f"pid {info.pid}, qnum {info.qnum if info.qnum is not None else '?'}, пользователь {info.user or '?'}",
            )
        )
    else:
        checks.append(
            Check(
                "zapret.process",
                "Процесс nfqws",
                FAIL,
                "процесс не найден",
                f"sudo systemctl restart {info.service}",
            )
        )

    status, details = zapret.nft_table_state(info.nft_table)
    if status == "ok":
        checks.append(Check("zapret.nft", "Правила nftables", PASS, details))
    elif status == "no-root":
        checks.append(
            Check(
                "zapret.nft",
                "Правила nftables",
                WARN,
                details,
                "Запустите doctor через sudo для полной проверки",
            )
        )
    else:
        checks.append(
            Check(
                "zapret.nft",
                "Правила nftables",
                FAIL,
                details,
                f"sudo systemctl restart {info.service}",
            )
        )
    return checks


def _hostlist_args_check(info: zapret.ZapretInfo) -> Check:
    """Профиль с путём без ``--hostlist=`` nfqws2 игнорирует.

    Это не поломка и не «применяется ко всем сайтам»: правило просто не
    работает. Раньше здесь был FAIL с советом «добавьте --hostlist=», но
    включение такого профиля ломало TLS — поэтому теперь это WARN с честным
    объяснением.
    """

    bare = zapret.find_bare_hostlist_args(info.cmdline)
    if not bare:
        return Check(
            "zapret.hostlist_args",
            "Аргументы hostlist",
            PASS,
            "все списки переданы через --hostlist",
        )
    names = ", ".join(Path(item).name for item in bare)
    return Check(
        "zapret.hostlist_args",
        "Аргументы hostlist",
        WARN,
        f"профиль игнорируется: {names} указан без --hostlist=. Правило не "
        "работает, остальные профили действуют как обычно",
        "Менять не нужно. Включать такой профиль вручную опасно: он может "
        "ломать TLS. Если понадобится — сначала blockcheck2.sh и проверка "
        "CF-доменов",
    )


def _domains_check(info: zapret.ZapretInfo, cfg: Config) -> Check:
    target = zapret.hostlist_target(info, cfg.zapret.hostlist)
    if not target:
        return Check(
            "domains.hostlist",
            "CF-домены в hostlist",
            FAIL,
            "hostlist не определён",
            "linfix domains list",
        )
    if not Path(target).exists():
        return Check(
            "domains.hostlist",
            "CF-домены в hostlist",
            FAIL,
            f"файл не найден: {target}",
            "linfix domains update",
        )

    state_info = state.load_state()
    domains = state_info.domains or FALLBACK_DOMAINS
    present = zapret.read_hostlist_domains(target)
    missing = [domain for domain in domains if domain not in present]
    count = len(domains) - len(missing)
    if not missing:
        return Check(
            "domains.hostlist",
            "CF-домены в hostlist",
            PASS,
            f"{count}/{len(domains)} в {target}",
        )
    status = FAIL if count == 0 else WARN
    return Check(
        "domains.hostlist",
        "CF-домены в hostlist",
        status,
        f"{count}/{len(domains)}; нет: {', '.join(missing[:3])}{'…' if len(missing) > 3 else ''}",
        "linfix domains update",
    )


def _state_check(cfg: Config) -> Check:
    state_info = state.load_state()
    if not state_info.domains_updated_at:
        return Check(
            "domains.state",
            "Состояние доменов",
            WARN,
            "state.json ещё не создан",
            "linfix domains update",
        )
    if state_info.last_error:
        first_line = state_info.last_error.splitlines()[0]
        return Check(
            "domains.state",
            "Состояние доменов",
            WARN,
            f"последняя ошибка: {first_line}",
            "linfix domains update && sudo systemctl restart zapret2",
        )
    try:
        updated = datetime.fromisoformat(state_info.domains_updated_at)
    except ValueError:
        return Check(
            "domains.state",
            "Состояние доменов",
            WARN,
            f"некорректная дата: {state_info.domains_updated_at}",
            "linfix domains update",
        )
    now = datetime.now(updated.tzinfo) if updated.tzinfo else datetime.now()
    days = (now - updated).days
    if days > cfg.lists.update_days:
        return Check(
            "domains.state",
            "Состояние доменов",
            WARN,
            f"обновлено {days} дн. назад (порог {cfg.lists.update_days})",
            "linfix update",
        )
    return Check(
        "domains.state",
        "Состояние доменов",
        PASS,
        f"{state_info.domains_count} доменов, обновлено {state_info.domains_updated_at}",
    )


def _tls_check(cfg: Config, timeout: float = 3.0) -> Check:
    state_info = state.load_state()
    domains = state_info.domains or FALLBACK_DOMAINS
    results = cfdomains.test_domains(domains, limit=2, timeout=timeout)
    if not results:
        return Check(
            "domains.tls",
            "CF-домены: TLS-тест",
            FAIL,
            "нет результатов",
            "linfix domains update",
        )
    ok_count = sum(1 for _, ok, _ in results if ok)
    total = len(results)
    details = f"{ok_count}/{total} (" + ", ".join(
        f"{host}: {detail}" for host, ok, detail in results if not ok
    ) + ")" if ok_count < total else f"{ok_count}/{total}"
    if ok_count == total:
        return Check("domains.tls", "CF-домены: TLS-тест", PASS, details)
    if ok_count == 0:
        return Check(
            "domains.tls",
            "CF-домены: TLS-тест",
            FAIL,
            details,
            "linfix domains update && sudo systemctl restart zapret2",
        )
    return Check(
        "domains.tls",
        "CF-домены: TLS-тест",
        WARN,
        details,
        "Проверьте linfix domains test --all",
    )


def _telegram_check(cfg: Config) -> Check:
    completed = util.run(["ss", "-tn"], check=False, timeout=10)
    count = 0
    for line in (completed.stdout or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) < 5:
            continue
        state_column = parts[0]
        local = parts[3]
        peer = parts[4]
        if state_column == "ESTAB" and (
            local.endswith(f":{cfg.tgws.port}") or peer.endswith(f":{cfg.tgws.port}")
        ):
            count += 1
    if count:
        return Check(
            "telegram.connections",
            "Telegram подключён",
            PASS,
            f"{count} соединений на 127.0.0.1:{cfg.tgws.port}",
        )
    return Check(
        "telegram.connections",
        "Telegram подключён",
        WARN,
        f"нет соединений на 127.0.0.1:{cfg.tgws.port}",
        "Откройте tg://-ссылку: linfix tgws link",
    )


def _tgws_checks(cfg: Config) -> list[Check]:
    checks: list[Check] = []
    binary = Path(cfg.tgws.binary)
    completed = util.run(["systemctl", "--user", "is-active", "tgwsproxy"], check=False, timeout=10)
    state = (completed.stdout or "").strip() or "inactive"
    if binary.exists() and completed.returncode == 0 and state == "active":
        checks.append(Check("tgws.service", "TgWsProxy: служба", PASS, f"active (user), {binary}"))
    elif binary.exists():
        checks.append(
            Check(
                "tgws.service",
                "TgWsProxy: служба",
                WARN,
                f"{state}, бинарник есть",
                "systemctl --user start tgwsproxy",
            )
        )
    else:
        checks.append(
            Check(
                "tgws.service",
                "TgWsProxy: служба",
                FAIL,
                f"не установлен ({binary})",
                "linfix tgws install (M2)",
            )
        )

    addresses = zapret.listening_addresses(cfg.tgws.port)
    if not addresses:
        checks.append(
            Check(
                "tgws.port",
                f"TgWsProxy: порт {cfg.tgws.port}",
                FAIL,
                "никто не слушает",
                "linfix tgws start",
            )
        )
    elif all(address.startswith("127.") or address.startswith("[::1]") for address in addresses):
        checks.append(
            Check(
                "tgws.port",
                f"TgWsProxy: порт {cfg.tgws.port}",
                PASS,
                f"LISTEN {', '.join(addresses)}",
            )
        )
    else:
        checks.append(
            Check(
                "tgws.port",
                f"TgWsProxy: порт {cfg.tgws.port}",
                FAIL,
                f"слушает не только localhost: {', '.join(addresses)}",
                "В конфиге TgWsProxy укажите host = 127.0.0.1",
            )
        )
    return checks


def _route_check() -> Check:
    completed = util.run(["ip", "route"], check=False, timeout=10)
    lines = [line.strip() for line in (completed.stdout or "").splitlines() if line.strip()]
    split_default = any(line.startswith(("0.0.0.0/1", "128.0.0.0/1")) for line in lines)
    tunnel_routes = [
        line
        for line in lines
        if " dev " in line
        and any(marker in line for marker in ("tun", "vpn", "wg"))
    ]

    # Интерфейс VPN может остаться без маршрутов (или маршруты ещё не подняты),
    # поэтому дополнительно смотрим список интерфейсов.
    link = util.run(["ip", "-brief", "link"], check=False, timeout=10)
    tunnel_interfaces: list[str] = []
    for line in (link.stdout or "").splitlines():
        parts = line.split()
        if parts and parts[0].startswith(("tun", "vpn", "wg")):
            tunnel_interfaces.append(parts[0])

    if split_default or tunnel_routes or tunnel_interfaces:
        if tunnel_routes:
            details = tunnel_routes[0]
        elif split_default:
            details = "split-default 0.0.0.0/1 + 128.0.0.0/1"
        else:
            details = f"интерфейс {', '.join(tunnel_interfaces)}"
        return Check(
            "route.vpn",
            "Маршрут по умолчанию",
            WARN,
            f"обнаружен VPN/TUN: {details}",
            "zapret может не видеть трафик, а TLS-тест — обходить DPI через VPN",
        )
    return Check("route.vpn", "Маршрут по умолчанию", PASS, "без VPN/TUN")


def _guard_check(cfg: Config) -> Check:
    if not cfg.guard.enabled:
        return Check("guard", "Guard", WARN, "выключен", "linfix guard on")

    event = guard.load_event()
    if event.get("last_trip"):
        detail = str(event.get("detail", ""))[:90]
        return Check(
            "guard",
            "Guard",
            WARN,
            f"последнее срабатывание {event.get('last_trip')}: {detail}",
            "linfix domains test && linfix tgws restart",
        )
    return Check(
        "guard",
        "Guard",
        PASS,
        f"включён, срабатываний не было (порог {cfg.guard.error_threshold}/{cfg.guard.window_seconds} с)",
    )


def run_doctor(
    cfg: Config,
    *,
    tls: bool = True,
    tls_timeout: float = 3.0,
) -> Report:
    info = zapret.detect_zapret(cfg.zapret.service)
    checks: list[Check] = [_os_check(), _nfqueue_check()]
    checks.extend(_zapret_checks(info))
    checks.append(_hostlist_args_check(info))
    checks.append(_domains_check(info, cfg))
    checks.append(_state_check(cfg))
    if tls:
        checks.append(_tls_check(cfg, tls_timeout))
    checks.extend(_tgws_checks(cfg))
    checks.append(_telegram_check(cfg))
    checks.append(_route_check())
    checks.append(_guard_check(cfg))
    return Report(tuple(checks))
