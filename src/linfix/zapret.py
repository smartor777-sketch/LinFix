"""Обнаружение zapret и работа с его hostlist (только чтение в M0)."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from . import util
from .cfdomains import parse_domain_text

SERVICE_CANDIDATES = ("zapret2", "zapret")
NFQWS_NAMES = ("nfqws", "nfqws2")
DEFAULT_NFT_TABLES = {"zapret2": "zapret2", "zapret": "zapret"}


@dataclass(frozen=True)
class NfqwsArgs:
    hostlists: tuple[str, ...] = ()
    hostlist_excludes: tuple[str, ...] = ()
    qnum: int | None = None
    fwmark: str | None = None
    user: str | None = None


@dataclass(frozen=True)
class ZapretInfo:
    found: bool
    flavor: str
    install_dir: str
    service: str
    binary: str
    pid: int | None
    cmdline: tuple[str, ...]
    hostlists: tuple[str, ...]
    hostlist_excludes: tuple[str, ...]
    qnum: int | None
    fwmark: str | None
    user: str | None
    nft_table: str | None
    active: bool
    enabled: bool
    enabled_state: str
    warnings: tuple[str, ...] = ()


def iter_processes() -> Iterable[tuple[int, list[str]]]:
    """Пройти по /proc и вернуть (pid, argv) для доступных процессов."""

    proc = Path("/proc")
    try:
        entries = list(proc.iterdir())
    except OSError:
        return
    for entry in entries:
        if not entry.name.isdigit():
            continue
        try:
            raw = entry.joinpath("cmdline").read_bytes()
        except OSError:
            continue
        args = [part.decode("utf-8", "replace") for part in raw.split(b"\0") if part]
        if args:
            yield int(entry.name), args


def find_nfqws_processes() -> list[tuple[int, list[str]]]:
    result: list[tuple[int, list[str]]] = []
    for pid, args in iter_processes():
        if Path(args[0]).name in NFQWS_NAMES:
            result.append((pid, args))
    return result


def parse_nfqws_args(args: list[str] | tuple[str, ...]) -> NfqwsArgs:
    """Разобрать аргументы nfqws/nfqws2.

    Поддерживаются формы ``--hostlist=/path`` и ``--hostlist /path``.
    """

    hostlists: list[str] = []
    excludes: list[str] = []
    qnum: int | None = None
    fwmark: str | None = None
    user: str | None = None

    argv = list(args)
    for index, arg in enumerate(argv):
        if arg.startswith("--hostlist-exclude="):
            excludes.append(arg.split("=", 1)[1])
        elif arg == "--hostlist-exclude" and index + 1 < len(argv):
            excludes.append(argv[index + 1])
        elif arg.startswith("--hostlist="):
            hostlists.append(arg.split("=", 1)[1])
        elif arg == "--hostlist" and index + 1 < len(argv):
            hostlists.append(argv[index + 1])
        elif arg.startswith("--qnum="):
            try:
                qnum = int(arg.split("=", 1)[1])
            except ValueError:
                qnum = None
        elif arg == "--qnum" and index + 1 < len(argv):
            try:
                qnum = int(argv[index + 1])
            except ValueError:
                qnum = None
        elif arg.startswith("--fwmark="):
            fwmark = arg.split("=", 1)[1]
        elif arg.startswith("--user="):
            user = arg.split("=", 1)[1]

    return NfqwsArgs(
        hostlists=tuple(hostlists),
        hostlist_excludes=tuple(excludes),
        qnum=qnum,
        fwmark=fwmark,
        user=user,
    )


def choose_hostlist(hostlists: Iterable[str]) -> str | None:
    """Выбрать записываемый hostlist.

    Приоритет: не сжатый файл с ``user`` в имени, затем любой не ``.gz``,
    затем первый из списка (например, если есть только ``.gz``).
    """

    candidates = [path for path in hostlists if path]
    plain = [path for path in candidates if not path.endswith(".gz")]
    for path in plain:
        if "user" in Path(path).name.lower():
            return path
    if plain:
        return plain[0]
    return candidates[0] if candidates else None


def infer_install_dir(binary: str) -> str:
    path = Path(binary)
    parent = path.parent
    if parent.name in {"nfq", "nfq2"}:
        return str(parent.parent)
    return str(parent)


def _systemctl(verb: str, unit: str) -> tuple[int, str]:
    completed = util.run(["systemctl", verb, unit], check=False, timeout=10)
    return completed.returncode, (completed.stdout or "").strip()


def unit_exists(unit: str) -> bool:
    completed = util.run(["systemctl", "cat", unit], check=False, timeout=10)
    return completed.returncode == 0


def detect_zapret(service: str | None = None) -> ZapretInfo:
    """Определить фактическую установку zapret.

    Работает без root: читает /proc и вызывает systemctl без изменения состояния.
    """

    warnings: list[str] = []
    processes = find_nfqws_processes()
    process = processes[0] if processes else None
    if len(processes) > 1:
        warnings.append(f"найдено несколько процессов nfqws: {[pid for pid, _ in processes]}")

    # Конфиг по умолчанию содержит service="zapret", но на машине может быть
    # установлен zapret2. Поэтому заданная служба — это подсказка, а не
    # жёсткое переопределение: при отсутствии юнита ищем фактический.
    process_service: str | None = None
    if process is not None:
        process_service = "zapret2" if Path(process[1][0]).name == "nfqws2" else "zapret"

    candidates: list[str] = []
    for candidate in ([service] if service else []) + list(SERVICE_CANDIDATES):
        if candidate and candidate not in candidates:
            candidates.append(candidate)
    existing_units = [candidate for candidate in candidates if unit_exists(candidate)]

    if (
        service
        and service in existing_units
        and (process_service is None or service == process_service)
    ):
        unit = service
    elif process_service and process_service in existing_units:
        unit = process_service
        if service and service != process_service:
            warnings.append(
                f"юнит {service} не найден или не соответствует процессу; использую {unit}"
            )
    elif existing_units:
        unit = existing_units[0]
        if service and service != unit:
            warnings.append(f"юнит {service} не найден; использую {unit}")
    elif process_service:
        unit = process_service
        warnings.append(f"юнит не найден, служба определена по процессу: {unit}")
    else:
        unit = service or SERVICE_CANDIDATES[0]
        if service:
            warnings.append(f"юнит {service} не найден")

    active_code, active_out = _systemctl("is-active", unit)
    active = active_code == 0 and active_out == "active"
    enabled_code, enabled_state = _systemctl("is-enabled", unit)
    enabled = enabled_code == 0 and enabled_state == "enabled"
    if not enabled_state:
        enabled_state = "unknown"

    pid: int | None = None
    cmdline: tuple[str, ...] = ()
    binary = ""
    if process is not None:
        pid, argv = process
        cmdline = tuple(argv)
        binary = argv[0]

    parsed = parse_nfqws_args(cmdline)
    flavor = ""
    if binary:
        if Path(binary).name == "nfqws2" or "zapret2" in unit:
            flavor = "zapret2"
        else:
            flavor = "zapret"
    elif "zapret2" in unit:
        flavor = "zapret2"
    elif unit:
        flavor = "zapret"

    install_dir = infer_install_dir(binary) if binary else ""
    if not install_dir and flavor:
        install_dir = "/opt/zapret2" if flavor == "zapret2" else "/opt/zapret"

    nft_table = DEFAULT_NFT_TABLES.get(flavor) if flavor else None
    found = active or process is not None

    if found and process is None:
        warnings.append("служба активна, но процесс nfqws не найден в /proc")
    if found and not parsed.hostlists:
        warnings.append("в аргументах nfqws не найден --hostlist")

    return ZapretInfo(
        found=found,
        flavor=flavor,
        install_dir=install_dir,
        service=unit,
        binary=binary,
        pid=pid,
        cmdline=cmdline,
        hostlists=parsed.hostlists,
        hostlist_excludes=parsed.hostlist_excludes,
        qnum=parsed.qnum,
        fwmark=parsed.fwmark,
        user=parsed.user,
        nft_table=nft_table,
        active=active,
        enabled=enabled,
        enabled_state=enabled_state,
        warnings=tuple(warnings),
    )


def hostlist_target(info: ZapretInfo, configured: str = "") -> str | None:
    if configured:
        return configured
    return choose_hostlist(info.hostlists)


def read_hostlist_domains(path: str | Path) -> set[str]:
    target = Path(path)
    if not target.exists():
        return set()
    return set(parse_domain_text(target.read_text(encoding="utf-8", errors="replace")))


def nft_table_state(table: str | None) -> tuple[str, str]:
    """Проверить наличие nft-таблицы zapret.

    Возвращает (``ok`` | ``no-root`` | ``missing`` | ``error``, детали).
    """

    if not table:
        return "missing", "имя nft-таблицы неизвестно"
    if util.which("nft") is None:
        return "missing", "nft не установлен"

    completed = util.run(["nft", "list", "table", "inet", table], check=False, timeout=15)
    if completed.returncode == 0:
        lines = [line for line in completed.stdout.splitlines() if line.strip()]
        return "ok", f"таблица inet {table}, строк: {len(lines)}"

    error = (completed.stderr or "").strip()
    if "Operation not permitted" in error or "must be root" in error or "Permission denied" in error:
        return "no-root", "нужен root, чтобы прочитать nftables"
    return "error", error or f"nft вернул код {completed.returncode}"


def listening_addresses(port: int) -> list[str]:
    """Адреса, слушающие указанный TCP-порт (по данным ``ss -ltn``)."""

    completed = util.run(["ss", "-ltn"], check=False, timeout=10)
    addresses: list[str] = []
    for line in (completed.stdout or "").splitlines()[1:]:
        parts = line.split()
        if len(parts) < 4:
            continue
        local = parts[3]
        if local.rsplit(":", 1)[-1] == str(port):
            addresses.append(local)
    return addresses


def find_bare_hostlist_args(cmdline: tuple[str, ...] | list[str]) -> list[str]:
    """Найти hostlist-файлы, переданные без --hostlist (баг конфига zapret).

    nfqws2 понимает только ``--hostlist=path``. Голый путь молча игнорируется:
    профиль становится «для всех TLS», и desync ломает каждый HTTPS-сайт.
    """

    bare: list[str] = []
    for argument in cmdline:
        if argument.startswith("-") or "=" in argument:
            continue
        if argument.endswith(".txt") or argument.endswith(".gz"):
            bare.append(argument)
        elif argument.startswith("/") and Path(argument).suffix in {".txt", ".gz"}:
            bare.append(argument)
    return bare


def config_hostlist_problems(config_path: str | Path = "/opt/zapret2/config") -> list[str]:
    """Строки конфига zapret, где hostlist передан без --hostlist."""

    target = Path(config_path)
    if not target.exists():
        return []
    problems: list[str] = []
    for line in target.read_text(encoding="utf-8", errors="replace").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if "--hostlist=" in stripped or "--hostlist-exclude=" in stripped:
            # в строке уже есть корректный флаг, но может быть и голый путь
            pass
        for token in stripped.split():
            if token.startswith("-") or "=" in token:
                continue
            if token.endswith(".txt") or token.endswith(".gz"):
                problems.append(stripped)
                break
    return problems


def read_profiles(config_path: str | Path = "/opt/zapret2/config") -> list[dict[str, object]]:
    """Прочитать профили desync из конфига zapret (только чтение)."""

    target = Path(config_path)
    default_path = str(config_path) == "/opt/zapret2/config"
    if not target.exists():
        if not default_path:
            return []
        for candidate in ("/opt/zapret/ipset", "/opt/zapret2/ipset"):
            if Path(candidate).exists():
                alternate = Path(candidate).parent / "config"
                if alternate.exists():
                    target = alternate
                    break
        else:
            return []

    text = target.read_text(encoding="utf-8", errors="replace")
    block = ""
    collecting = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith('NFQWS2_OPT="'):
            collecting = True
            block = stripped[len('NFQWS2_OPT="') :]
            if block.endswith('"'):
                block = block[:-1]
                break
            continue
        if collecting:
            if stripped == '"':
                break
            block += "\n" + stripped

    profiles: list[dict[str, object]] = []
    for raw in block.split("--new"):
        line = raw.strip()
        if not line or not line.startswith("--"):
            continue
        tokens = line.split()
        hostlists = [t.split("=", 1)[1] for t in tokens if t.startswith("--hostlist=")]
        bare = [
            t for t in tokens
            if not t.startswith("-") and (t.endswith(".txt") or t.endswith(".gz"))
        ]
        desync = [t.split("=", 1)[1] for t in tokens if t.startswith("--lua-desync=")]
        uses_placeholder = any("<HOSTLIST" in t for t in tokens)
        l7 = ""
        port = ""
        for token in tokens:
            if token.startswith("--filter-l7="):
                l7 = token.split("=", 1)[1]
            elif token.startswith("--filter-tcp="):
                port = f"tcp/{token.split('=', 1)[1]}"
            elif token.startswith("--filter-udp="):
                port = f"udp/{token.split('=', 1)[1]}"
        proto = " ".join(item for item in (port, l7) if item) or "?"
        profiles.append(
            {
                "protocol": proto or "?",
                "hostlists": [Path(item).name for item in hostlists],
                # Полные пути нужны, чтобы понять, покрывает ли профиль домен.
                "hostlist_paths": list(hostlists),
                "bare_hostlists": [Path(item).name for item in bare],
                "bare_hostlist_paths": list(bare),
                "desync": desync,
                "uses_placeholder": uses_placeholder,
                "applies_to": (
                    "hostlist zapret"
                    if uses_placeholder
                    else "только свой список"
                    if hostlists
                    else "весь трафик (список без --hostlist не фильтрует)"
                    if bare
                    else "весь трафик"
                ),
                # nfqws2 применяет все профили. «Голый» путь не ограничивает
                # домены, поэтому такой профиль работает глобально (это
                # подтверждено debug-логом: он перехватил kws1.*.co.uk).
                "active": True,
                "raw": line,
            }
        )
    return profiles


def active_profile_for(profiles: list[dict[str, object]], domain: str) -> dict[str, object] | None:
    """Какой профиль первым совпадёт с доменом (учитывая порядок).

    nfqws2 выбирает первое совпавшее правило. Профиль без фильтра по
    hostlist (в том числе с «голым» путём без ``--hostlist=``) совпадает с
    любым доменом и поэтому перекрывает последующие профили.
    """

    host = domain.lower()

    def covers(profile: dict[str, object]) -> bool:
        """Покрывает ли профиль этот домен (по содержимому его hostlist)."""

        if profile.get("uses_placeholder"):
            # <HOSTLIST> — общий список zapret; покрытие проверит вызывающий
            # по факту наличия домена, здесь считаем «подходит».
            return True
        for path in list(profile.get("hostlist_paths") or []):
            try:
                if host in read_hostlist_domains(str(path)):
                    return True
            except OSError:
                continue
        return False

    for profile in profiles:
        if not profile.get("active"):
            continue
        listed = list(profile.get("hostlists") or [])
        bare = list(profile.get("bare_hostlist_paths") or [])
        if listed and covers(profile):
            return profile
        if listed:
            continue
        # Профиль без --hostlist (в том числе «голый» путь) глобален, но
        # срабатывает лишь если ни один профиль выше по порядку не совпал.
        if not profile.get("uses_placeholder"):
            return profile

    for profile in profiles:
        if profile.get("active") and profile.get("uses_placeholder"):
            return profile
    return None
