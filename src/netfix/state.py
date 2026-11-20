"""Состояние NetFix: /var/lib/netfix/state.json.

Пишет только helper (root). CLI читает как обычный пользователь.
Храним не только дату/хэш, но и сам список CF-доменов — это наш источник
правды, чтобы повторно применить домены после обновления/переустановки zapret.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path

from . import util

LOG = logging.getLogger("netfix.state")

DEFAULT_STATE_PATH = Path("/var/lib/netfix/state.json")
STATE_VERSION = 1


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def default_state_path() -> Path:
    return Path(os.environ.get("NETFIX_STATE", str(DEFAULT_STATE_PATH)))


def domains_digest(domains: list[str] | tuple[str, ...]) -> str:
    """SHA256 нормализованного набора доменов (порядок не важен)."""

    normalized = sorted({domain.strip().lower().rstrip(".") for domain in domains if domain.strip()})
    payload = "\n".join(normalized) + "\n"
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class State:
    version: int = STATE_VERSION
    updated_at: str | None = None
    domains_updated_at: str | None = None
    domains_sha256: str | None = None
    domains_count: int = 0
    domains_source: str | None = None
    domains_url: str | None = None
    domains: tuple[str, ...] = ()
    hostlist: str | None = None
    service: str | None = None
    last_error: str | None = None
    tgws_version: str | None = None
    tgws_sha256: str | None = None
    tgws_installed_at: str | None = None
    tgws_binary: str | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "version": self.version,
            "updated_at": self.updated_at,
            "domains_updated_at": self.domains_updated_at,
            "domains_sha256": self.domains_sha256,
            "domains_count": self.domains_count,
            "domains_source": self.domains_source,
            "domains_url": self.domains_url,
            "domains": list(self.domains),
            "hostlist": self.hostlist,
            "service": self.service,
            "last_error": self.last_error,
            "tgws_version": self.tgws_version,
            "tgws_sha256": self.tgws_sha256,
            "tgws_installed_at": self.tgws_installed_at,
            "tgws_binary": self.tgws_binary,
        }

    @classmethod
    def from_dict(cls, raw: dict[str, object]) -> "State":
        domains_raw = raw.get("domains", [])
        domains: tuple[str, ...] = ()
        if isinstance(domains_raw, list):
            domains = tuple(str(item) for item in domains_raw if isinstance(item, str))
        return cls(
            version=int(raw.get("version", STATE_VERSION)) if isinstance(raw.get("version"), int) else STATE_VERSION,
            updated_at=str(raw["updated_at"]) if isinstance(raw.get("updated_at"), str) else None,
            domains_updated_at=str(raw["domains_updated_at"]) if isinstance(raw.get("domains_updated_at"), str) else None,
            domains_sha256=str(raw["domains_sha256"]) if isinstance(raw.get("domains_sha256"), str) else None,
            domains_count=int(raw["domains_count"]) if isinstance(raw.get("domains_count"), int) else 0,
            domains_source=str(raw["domains_source"]) if isinstance(raw.get("domains_source"), str) else None,
            domains_url=str(raw["domains_url"]) if isinstance(raw.get("domains_url"), str) else None,
            domains=domains,
            hostlist=str(raw["hostlist"]) if isinstance(raw.get("hostlist"), str) else None,
            service=str(raw["service"]) if isinstance(raw.get("service"), str) else None,
            last_error=str(raw["last_error"]) if isinstance(raw.get("last_error"), str) else None,
            tgws_version=str(raw["tgws_version"]) if isinstance(raw.get("tgws_version"), str) else None,
            tgws_sha256=str(raw["tgws_sha256"]) if isinstance(raw.get("tgws_sha256"), str) else None,
            tgws_installed_at=str(raw["tgws_installed_at"]) if isinstance(raw.get("tgws_installed_at"), str) else None,
            tgws_binary=str(raw["tgws_binary"]) if isinstance(raw.get("tgws_binary"), str) else None,
        )

    def record_domains(
        self,
        domains: list[str] | tuple[str, ...],
        *,
        source_label: str | None,
        source_url: str | None,
        hostlist: str | Path | None,
        service: str | None,
    ) -> "State":
        stamp = now_iso()
        return replace(
            self,
            updated_at=stamp,
            domains_updated_at=stamp,
            domains_sha256=domains_digest(domains),
            domains_count=len(domains),
            domains_source=source_label,
            domains_url=source_url,
            domains=tuple(domains),
            hostlist=str(hostlist) if hostlist is not None else None,
            service=service,
            last_error=None,
        )

    def record_tgws(self, version: str, sha256: str, binary: str | Path) -> "State":
        return replace(
            self,
            updated_at=now_iso(),
            tgws_version=version,
            tgws_sha256=sha256,
            tgws_installed_at=now_iso(),
            tgws_binary=str(binary),
            last_error=None,
        )

    def record_error(self, message: str) -> "State":
        return replace(self, updated_at=now_iso(), last_error=message[-4000:])

    def save(self, path: str | Path | None = None) -> Path:
        target = Path(path) if path is not None else default_state_path()
        payload = json.dumps(self.to_dict(), ensure_ascii=False, indent=2) + "\n"
        util.atomic_write(target, payload, mode=0o644)
        return target


def load_state(path: str | Path | None = None) -> State:
    target = Path(path) if path is not None else default_state_path()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return State()
    except (OSError, json.JSONDecodeError) as exc:
        LOG.warning("не удалось прочитать состояние %s: %s", target, exc)
        return State(last_error=f"не удалось прочитать state: {exc}")

    if not isinstance(raw, dict):
        return State(last_error="state.json должен быть JSON-объектом")
    return State.from_dict(raw)
