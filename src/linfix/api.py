"""Высокоуровневый API LinFix, общий для CLI и будущей GTK-панели.

Панель не должна дублировать логику: она импортирует этот модуль, а
привилегированные действия вызывает через ``pkexec /usr/libexec/linfix-helper``.
"""

from __future__ import annotations

from dataclasses import dataclass

from . import cfdomains, doctor, state, tgws, zapret
from .config import Config


@dataclass(frozen=True)
class Status:
    zapret: zapret.ZapretInfo
    hostlist: str | None
    cf_present: int
    cf_total: int
    tgws_installed: bool
    tgws_state: str
    listeners: tuple[str, ...]
    config_source: str | None
    state_info: state.State


def get_status(cfg: Config) -> Status:
    info = zapret.detect_zapret(cfg.zapret.service)
    hostlist = zapret.hostlist_target(info, cfg.zapret.hostlist)
    present = zapret.read_hostlist_domains(hostlist) if hostlist else set()
    cf_present = sum(1 for domain in cfdomains.FALLBACK_DOMAINS if domain in present)
    installed = tgws.is_installed(cfg.tgws.binary)
    return Status(
        zapret=info,
        hostlist=hostlist,
        cf_present=cf_present,
        cf_total=len(cfdomains.FALLBACK_DOMAINS),
        tgws_installed=installed,
        tgws_state=tgws.service_state() if installed else "не установлен",
        listeners=tuple(zapret.listening_addresses(cfg.tgws.port)),
        config_source=str(cfg.source) if cfg.source else None,
        state_info=state.load_state(),
    )


def get_doctor_report(
    cfg: Config,
    *,
    tls: bool = True,
    tls_timeout: float = 3.0,
) -> doctor.Report:
    return doctor.run_doctor(cfg, tls=tls, tls_timeout=tls_timeout)


def get_domain_source(
    cfg: Config,
    *,
    offline: bool = False,
    timeout: float = 15.0,
) -> cfdomains.DomainSource:
    return cfdomains.load_domain_source(
        cfg.lists.cf_domains_url, offline=offline, timeout=timeout
    )


def get_domains(
    cfg: Config,
    *,
    offline: bool = False,
    timeout: float = 15.0,
) -> tuple[cfdomains.DomainSource, str | None, set[str]]:
    """Вернуть (источник доменов, целевой hostlist, домены в hostlist)."""

    info = zapret.detect_zapret(cfg.zapret.service)
    hostlist = zapret.hostlist_target(info, cfg.zapret.hostlist)
    present = zapret.read_hostlist_domains(hostlist) if hostlist else set()
    return get_domain_source(cfg, offline=offline, timeout=timeout), hostlist, present
