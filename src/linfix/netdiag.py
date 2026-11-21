"""Сетевая диагностика: проверки уровня NetFix, но для Linux/zapret2.

Что проверяем и зачем:

- **DC Telegram** — TCP до дата-центров на 443/80/5222. Показывает, живёт ли
  прямой путь (без прокси он обычно заблокирован).
- **Пинг до опорных узлов** — есть ли интернет вообще.
- **DNS-спуфинг** — сравниваем ответ системного DNS с DoH (1.1.1.1).
- **SNI/DPI** — один и тот же IP, два имени: telegram.org и нейтральный.
  Если блокируется только «своё» имя — режут по SNI.
- **Троттлинг** — сравниваем задержку до DC Telegram с задержкой до опорных
  узлов: аномальная разница указывает на замедление.
- **Медиа** — реальная скорость загрузки файла Telegram.

Итог — «человеческий вердикт» и список рекомендаций, как в NetFix.
"""

from __future__ import annotations

import socket
import ssl
import statistics
import time
import urllib.request
from dataclasses import dataclass, field

TELEGRAM_DCS: tuple[tuple[int, str], ...] = (
    (1, "149.154.175.50"),
    (2, "149.154.167.51"),
    (3, "149.154.175.100"),
    (4, "149.154.167.91"),
    (5, "91.108.56.130"),
)
TELEGRAM_PORTS: tuple[int, ...] = (443, 80, 5222)
PING_HOSTS: tuple[str, ...] = ("1.1.1.1", "8.8.8.8", "77.88.8.8")
MEDIA_URLS: tuple[str, ...] = (
    "https://web.telegram.org/a/runtime.js",
)
SLOW_MEDIA_KBPS = 200.0
DNI_PROBE_IP = "149.154.167.51"

BLOCK_NONE = "none"
BLOCK_SNI = "sni"
BLOCK_IP = "ip"
BLOCK_THROTTLE = "throttle"
BLOCK_DNS = "dns"
BLOCK_MEDIA = "media"
BLOCK_NO_INTERNET = "no_internet"


@dataclass
class DcResult:
    dc_id: int
    ip: str
    port: int
    ok: bool
    latency_ms: float | None = None
    error: str = ""


@dataclass
class PingResult:
    host: str
    ok: bool
    latency_ms: float | None = None


@dataclass
class DnsResult:
    domain: str
    system_ips: list[str] = field(default_factory=list)
    doh_ips: list[str] = field(default_factory=list)
    spoofed: bool = False
    error: str = ""


@dataclass
class DpiResult:
    sni_target: str = "unknown"
    sni_neutral: str = "unknown"
    dpi_detected: bool = False


@dataclass
class MediaResult:
    speed_kbps: float = 0.0
    throttled: bool = False
    error: str = ""


@dataclass
class NetReport:
    dc: list[DcResult] = field(default_factory=list)
    ping: list[PingResult] = field(default_factory=list)
    dns: DnsResult | None = None
    dpi: DpiResult | None = None
    media: MediaResult | None = None
    blocks: list[str] = field(default_factory=list)
    verdict: tuple[str, str, str] = ("", "", "")  # emoji, title, detail
    recommendations: list[str] = field(default_factory=list)

    def as_rows(self) -> list[tuple[str, str, str]]:
        """Плоский список для таблицы «Проверка / Статус / Детали»."""

        rows: list[tuple[str, str, str]] = []
        for item in self.dc:
            state = f"{item.latency_ms:.0f} мс" if item.ok else (item.error or "недоступен")
            rows.append((f"DC{item.dc_id} {item.ip}:{item.port}", "ok" if item.ok else "fail", state))
        for item in self.ping:
            state = f"{item.latency_ms:.0f} мс" if item.ok else "нет ответа"
            rows.append((f"Пинг {item.host}", "ok" if item.ok else "warn", state))
        if self.dns is not None:
            if self.dns.error:
                rows.append(("DNS", "warn", self.dns.error))
            else:
                rows.append(
                    (
                        f"DNS {self.dns.domain}",
                        "fail" if self.dns.spoofed else "ok",
                        (
                            "подменён: система "
                            + (", ".join(self.dns.system_ips[:2]) or "—")
                            + " / DoH "
                            + (", ".join(self.dns.doh_ips[:2]) or "—")
                            if self.dns.spoofed
                            else ", ".join(self.dns.system_ips[:2])
                        ),
                    )
                )
        if self.dpi is not None:
            rows.append(
                (
                    "Блокировка по SNI",
                    "fail" if self.dpi.dpi_detected else "ok",
                    (
                        f"telegram.org: {self.dpi.sni_target}, нейтральный: {self.dpi.sni_neutral}"
                    ),
                )
            )
        if self.media is not None:
            if self.media.error:
                rows.append(("Медиа Telegram", "warn", self.media.error))
            else:
                rows.append(
                    (
                        "Медиа Telegram",
                        "warn" if self.media.throttled else "ok",
                        f"{self.media.speed_kbps:.0f} кбит/с (норма > {SLOW_MEDIA_KBPS:.0f})",
                    )
                )
        return rows


# ---------------------------------------------------------------------------
# Примитивы
# ---------------------------------------------------------------------------


def tcp_connect(ip: str, port: int, timeout: float = 3.0) -> tuple[bool, float]:
    started = time.monotonic()
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True, (time.monotonic() - started) * 1000
    except (socket.timeout, TimeoutError):
        return False, 0.0
    except OSError:
        return False, 0.0


def tls_state(ip: str, sni: str, timeout: float = 4.0) -> str:
    """Результат TLS-хендшейка: ok | timeout | rst | error:…"""

    context = ssl.create_default_context()
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    try:
        with socket.create_connection((ip, 443), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=sni):
                return "ok"
    except (socket.timeout, TimeoutError):
        return "timeout"
    except ConnectionResetError:
        return "rst"
    except ssl.SSLError as exc:
        return f"error:{getattr(exc, 'reason', None) or exc}"
    except OSError as exc:
        return f"error:{exc}"


def ping_once(host: str, timeout: float = 2.0) -> tuple[bool, float]:
    from .util import run

    completed = run(
        ["ping", "-4", "-c", "1", "-W", str(max(1, int(timeout))), host],
        check=False,
        timeout=timeout + 2,
    )
    if completed.returncode != 0:
        return False, 0.0
    for line in (completed.stdout or "").splitlines():
        if "time=" in line:
            try:
                return True, float(line.split("time=")[1].split()[0])
            except (IndexError, ValueError):
                break
    return True, 0.0


def resolve(domain: str) -> list[str]:
    """Адреса от системного резолвера."""

    try:
        return sorted({info[4][0] for info in socket.getaddrinfo(domain, 53)})
    except socket.gaierror:
        return []


def resolve_doh(domain: str, server: str = "1.1.1.1", timeout: float = 4.0) -> list[str]:
    """Резолв через DNS-over-HTTPS (Cloudflare JSON API)."""

    url = f"https://{server}/dns-query?name={domain}&type=A"
    request = urllib.request.Request(url, headers={"accept": "application/dns-json"})
    try:
        import json

        with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
            data = json.loads(response.read().decode("utf-8", "replace"))
    except Exception:  # noqa: BLE001 — нет DoH, это не критично
        return []
    return [item.get("data", "") for item in data.get("Answer", []) if item.get("type") == 1]


# ---------------------------------------------------------------------------
# Проверки
# ---------------------------------------------------------------------------


def check_dc(timeout: float = 3.0) -> list[DcResult]:
    """TCP до всех DC параллельно: последовательно это занимает минуту."""

    from concurrent.futures import ThreadPoolExecutor

    tasks = [(dc_id, ip, port) for dc_id, ip in TELEGRAM_DCS for port in TELEGRAM_PORTS]
    results: list[DcResult] = []
    with ThreadPoolExecutor(max_workers=15) as pool:
        futures = {
            pool.submit(tcp_connect, ip, port, timeout): (dc_id, ip, port)
            for dc_id, ip, port in tasks
        }
        for future, (dc_id, ip, port) in futures.items():
            ok, latency = future.result()
            results.append(
                DcResult(dc_id, ip, port, ok, latency if ok else None, "" if ok else "timeout")
            )
    results.sort(key=lambda item: (item.dc_id, item.port))
    return results


def check_ping(timeout: float = 2.0) -> list[PingResult]:
    from concurrent.futures import ThreadPoolExecutor

    with ThreadPoolExecutor(max_workers=len(PING_HOSTS)) as pool:
        results = list(pool.map(lambda host: PingResult(host, *ping_once(host, timeout)), PING_HOSTS))
    return results


def check_dns(domain: str = "telegram.org") -> DnsResult:
    result = DnsResult(domain)
    result.system_ips = resolve(domain)
    result.doh_ips = resolve_doh(domain)
    if not result.system_ips:
        result.spoofed = True
        result.error = "системный DNS не отвечает"
    elif not result.doh_ips:
        result.error = "DoH недоступен (сравнить не с чем)"
    elif not set(result.system_ips) & set(result.doh_ips):
        result.spoofed = True
    return result


def check_dpi(timeout: float = 4.0) -> DpiResult:
    result = DpiResult()
    result.sni_target = tls_state(DNI_PROBE_IP, "telegram.org", timeout)
    result.sni_neutral = tls_state(DNI_PROBE_IP, "example.com", timeout)
    result.dpi_detected = result.sni_target in {"rst", "timeout"} and result.sni_neutral not in {
        "rst",
        "timeout",
    }
    return result


def check_media(timeout: float = 12.0) -> MediaResult:
    result = MediaResult()
    for url in MEDIA_URLS:
        started = time.monotonic()
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:  # noqa: S310
                payload = response.read(512 * 1024)
        except Exception as exc:  # noqa: BLE001
            result.error = str(exc)
            continue
        elapsed = max(time.monotonic() - started, 0.001)
        size_kb = len(payload) / 1024
        if size_kb < 5:
            continue
        result.speed_kbps = size_kb / elapsed * 8
        result.throttled = result.speed_kbps < SLOW_MEDIA_KBPS
        result.error = ""
        return result
    return result


# ---------------------------------------------------------------------------
# Классификация и вердикт
# ---------------------------------------------------------------------------


def classify(report: NetReport) -> list[str]:
    blocks: list[str] = []
    dc_ok = [item for item in report.dc if item.ok]
    ping_ok = [item for item in report.ping if item.ok]

    if not ping_ok and not dc_ok:
        return [BLOCK_NO_INTERNET]
    if report.dns is not None and report.dns.spoofed:
        blocks.append(BLOCK_DNS)
    if report.dpi is not None and report.dpi.dpi_detected:
        blocks.append(BLOCK_SNI)
    if not dc_ok and ping_ok and BLOCK_SNI not in blocks:
        blocks.append(BLOCK_IP)
    if dc_ok and ping_ok:
        dc_lat = [item.latency_ms for item in dc_ok if item.latency_ms is not None]
        ping_lat = [item.latency_ms for item in ping_ok if item.latency_ms]
        if dc_lat and ping_lat:
            avg_dc = statistics.fmean(dc_lat)
            avg_ping = statistics.fmean(ping_lat)
            if avg_dc > 250 or (avg_dc > 150 and avg_dc > avg_ping * 3):
                blocks.append(BLOCK_THROTTLE)
    if report.media is not None and report.media.throttled:
        blocks.append(BLOCK_MEDIA)
    return blocks


def verdict(report: NetReport, *, proxy_running: bool, zapret_running: bool) -> tuple[str, str, str]:
    blocks = report.blocks
    if proxy_running:
        return (
            "🟢",
            "TgWsProxy активен",
            "Telegram должен работать через прокси. Прямые проверки ниже могут "
            "показывать блокировки — это нормально, трафик идёт через прокси.",
        )
    if BLOCK_NO_INTERNET in blocks:
        return ("🔴", "Интернета нет", "Ни один узел не отвечает. Проверьте Wi-Fi или кабель.")
    if BLOCK_SNI in blocks:
        return (
            "🟢" if zapret_running else "🔴",
            "Блокировка по SNI" if not zapret_running else "SNI-блокировка, zapret компенсирует",
            "Провайдер обрывает TLS по имени. zapret маскирует SNI — добавьте домен в hostlist."
            if not zapret_running
            else "DPI режет имя, но zapret уже включён и это обходит.",
        )
    if BLOCK_IP in blocks:
        return (
            "🟡",
            "Серверы Telegram недоступны напрямую",
            "IP дата-центров заблокированы. Нужен TgWsProxy или VPN.",
        )
    if BLOCK_MEDIA in blocks:
        return (
            "🟡",
            "Медиа загружается медленно",
            f"Скорость {report.media.speed_kbps:.0f} кбит/с при норме > {SLOW_MEDIA_KBPS:.0f}. "
            "Обычно это ограничение со стороны провайдера.",
        )
    if BLOCK_THROTTLE in blocks:
        return ("🟡", "Похоже на замедление", "Задержка до Telegram заметно выше фоновой.")
    if BLOCK_DNS in blocks:
        return ("🟡", "Подмена DNS", "Системный DNS отдаёт не те адреса, что DoH.")
    dc_ok = sum(1 for item in report.dc if item.ok)
    if dc_ok >= max(len(report.dc) // 2, 1):
        return ("🟢", "Telegram работает нормально", "Серверы Telegram отвечают.")
    return ("🟡", "Ситуация неоднозначная", "Часть проверок прошла, часть нет — смотрите детали.")


def recommendations(report: NetReport, *, zapret_running: bool) -> list[str]:
    blocks = report.blocks
    tips: list[str] = []
    if BLOCK_NO_INTERNET in blocks:
        tips.append("Проверьте подключение: Wi-Fi/кабель, роутер, DHCP.")
    if BLOCK_DNS in blocks:
        tips.append(
            "DNS подменяется: смените сервер (например, 1.1.1.1) или включите DoH. "
            "Проверка: «Сайты» → ваш домен."
        )
    if BLOCK_SNI in blocks and not zapret_running:
        tips.append(
            "SNI-блокировка: включите zapret и добавьте домен в hostlist "
            "(«Сайты» → «Добавить в hostlist»)."
        )
    if BLOCK_IP in blocks:
        tips.append(
            "IP дата-центров недоступны напрямую: используйте TgWsProxy "
            "(«Мастер настройки» ставит его) или VPN."
        )
    if BLOCK_MEDIA in blocks:
        tips.append(
            "Медиа замедлено: это ограничение провайдера. Помогает прокси для "
            "медиа или VPN; настройки zapret тут не помогут."
        )
    if BLOCK_THROTTLE in blocks:
        tips.append("Высокая задержка до Telegram: попробуйте другое подключение или VPN.")
    if not tips:
        tips.append("Всё в порядке: критичных проблем не найдено.")
    return tips


def run_network_diagnostics(
    *,
    proxy_running: bool = False,
    zapret_running: bool = False,
    timeout: float = 3.0,
) -> NetReport:
    from concurrent.futures import ThreadPoolExecutor

    report = NetReport()
    with ThreadPoolExecutor(max_workers=5) as pool:
        ping_f = pool.submit(check_ping, timeout)
        dc_f = pool.submit(check_dc, timeout)
        dns_f = pool.submit(check_dns)
        dpi_f = pool.submit(check_dpi, timeout + 1)
        media_f = pool.submit(check_media)
        report.ping = ping_f.result()
        report.dc = dc_f.result()
        report.dns = dns_f.result()
        report.dpi = dpi_f.result()
        report.media = media_f.result()
    report.blocks = classify(report)
    report.verdict = verdict(report, proxy_running=proxy_running, zapret_running=zapret_running)
    report.recommendations = recommendations(report, zapret_running=zapret_running)
    return report
