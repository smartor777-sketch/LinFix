"""Проверка профиля zapret для CF-доменов TgWsProxy.

Зачем отдельная проверка: штатный `blockcheck2` ищет стратегию, которая
открывает домен, и не замечает главную проблему — профиль `tcpseg` пропускает
TLS, но ломает передачу больших тел через HTTP/2. Медиа в Telegram при этом
не грузится, хотя «домен открывается».

Проверка делает два шага для каждого домена:

1. TLS-хендшейк с SNI домена;
2. HTTP/2 `POST /api` телом 256 КБ.

Рабочий профиль отвечает на оба шага; при `tcpseg` второй шаг уходит в
`ReadTimeout`/`WriteTimeout`.
"""

from __future__ import annotations

import ssl
import socket
import time
from dataclasses import dataclass

#: Размер тела для проверки H2. 256 КБ с запасом перекрывает порог, на котором
#: `tcpseg` начинает ломать передачу.
BODY_BYTES = 256 * 1024

#: Сколько доменов проверять по умолчанию.
DEFAULT_LIMIT = 3


@dataclass(frozen=True)
class DomainProbe:
    domain: str
    host: str
    tls_ok: bool
    tls_ms: int
    h2_status: int | None
    h2_ms: int
    h2_error: str
    body_ok: bool

    def to_dict(self) -> dict[str, object]:
        return {
            "domain": self.domain,
            "host": self.host,
            "tls_ok": self.tls_ok,
            "tls_ms": self.tls_ms,
            "h2_status": self.h2_status,
            "h2_ms": self.h2_ms,
            "h2_error": self.h2_error,
            "body_ok": self.body_ok,
        }


@dataclass(frozen=True)
class ProfileReport:
    probes: tuple[DomainProbe, ...]
    tls_ok: bool
    body_ok: bool
    detection: str
    hint: str

    def to_dict(self) -> dict[str, object]:
        return {
            "probes": [probe.to_dict() for probe in self.probes],
            "tls_ok": self.tls_ok,
            "body_ok": self.body_ok,
            "detection": self.detection,
            "hint": self.hint,
            "body_bytes": BODY_BYTES,
        }


def _tls_probe(host: str, timeout: float) -> tuple[bool, int, str]:
    started = time.monotonic()
    context = ssl.create_default_context()
    try:
        with socket.create_connection((host, 443), timeout=timeout) as raw:
            with context.wrap_socket(raw, server_hostname=host) as tls:
                version = tls.version()
        return bool(version), int((time.monotonic() - started) * 1000), ""
    except Exception as exc:
        return False, int((time.monotonic() - started) * 1000), type(exc).__name__


def _h2_probe(host: str, timeout: float) -> tuple[int | None, int, str]:
    """POST 256 КБ через HTTP/2. Возвращает (статус, мс, ошибка)."""

    try:
        import logging

        import httpx
    except ImportError:  # pragma: no cover - httpx есть в зависимостях
        return None, 0, "httpx не установлен"

    import os

    # httpx пишет INFO на каждый запрос — в CLI это лишний шум.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    started = time.monotonic()
    try:
        with httpx.Client(
            http2=True,
            timeout=httpx.Timeout(timeout, connect=timeout, pool=timeout),
            trust_env=False,
        ) as client:
            response = client.post(
                f"https://{host}/api",
                content=os.urandom(BODY_BYTES),
                headers={
                    "content-type": "application/octet-stream",
                    "accept-encoding": "identity",
                },
            )
        return response.status_code, int((time.monotonic() - started) * 1000), ""
    except Exception as exc:
        return None, int((time.monotonic() - started) * 1000), type(exc).__name__


def probe_domain(domain: str, *, dc: int = 2, timeout: float = 8.0) -> DomainProbe:
    host = f"kws{dc}.{domain}"
    tls_ok, tls_ms, tls_error = _tls_probe(host, timeout)
    if not tls_ok:
        return DomainProbe(
            domain=domain,
            host=host,
            tls_ok=False,
            tls_ms=tls_ms,
            h2_status=None,
            h2_ms=0,
            h2_error=tls_error or "TLS",
            body_ok=False,
        )
    status, h2_ms, h2_error = _h2_probe(host, timeout)
    return DomainProbe(
        domain=domain,
        host=host,
        tls_ok=True,
        tls_ms=tls_ms,
        h2_status=status,
        h2_ms=h2_ms,
        h2_error=h2_error,
        body_ok=status is not None,
    )


def check_profile(
    domains: list[str] | tuple[str, ...],
    *,
    limit: int = DEFAULT_LIMIT,
    timeout: float = 8.0,
    dc: int = 2,
) -> ProfileReport:
    if not domains:
        return ProfileReport(
            probes=(),
            tls_ok=False,
            body_ok=False,
            detection="нет доменов для проверки",
            hint="Добавьте CF-домены в разделе «Домены»",
        )

    probes = tuple(
        probe_domain(domain, dc=dc, timeout=timeout) for domain in list(domains)[:limit]
    )
    tls_ok = all(probe.tls_ok for probe in probes)
    body_ok = all(probe.body_ok for probe in probes)

    if tls_ok and body_ok:
        return ProfileReport(
            probes=probes,
            tls_ok=True,
            body_ok=True,
            detection="профиль пропускает большие H2-тела — медиа должно грузиться",
            hint="",
        )
    if not tls_ok:
        return ProfileReport(
            probes=probes,
            tls_ok=False,
            body_ok=False,
            detection="TLS не проходит — CF-домены не открываются",
            hint="Проверьте, что zapret запущен и домены есть в hostlist",
        )
    return ProfileReport(
        probes=probes,
        tls_ok=True,
        body_ok=False,
        detection=(
            "TLS проходит, но H2-тело не передаётся — профиль ломает медиа "
            "(обычно tcpseg)"
        ),
        hint="Примените профиль CF: sudo scripts/apply-cf-profile.sh",
    )
