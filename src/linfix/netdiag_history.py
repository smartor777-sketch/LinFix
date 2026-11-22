"""История прогонов диагностики Telegram (SQLite).

Зачем база: результаты нужны между запусками панели, с датами и быстрым
доступом к последним прогонам. Для 20 доменов-справочников база была бы
избыточна, а для истории замеров — в самый раз.

Таблица одна и плоская: сводка прогона плюс полный отчёт в JSON, чтобы
не плодить схемы под каждую проверку.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import netdiag

#: Сколько прогонов хранить. Старые удаляются при вставке.
KEEP_RUNS = 100


@dataclass(frozen=True)
class HistoryEntry:
    run_id: int
    created_at: str
    verdict_emoji: str
    verdict_title: str
    direct_ok: bool
    proxy_ok: bool
    media_kbps: float
    payload: str

    @property
    def created_display(self) -> str:
        """«23.11 02:15» — компактно для таблицы."""

        try:
            moment = datetime.fromisoformat(self.created_at)
        except ValueError:
            return self.created_at
        return moment.strftime("%d.%m %H:%M")

    def report(self) -> netdiag.NetReport | None:
        try:
            return report_from_dict(json.loads(self.payload))
        except (ValueError, KeyError, TypeError):
            return None


def state_dir(home: str | Path | None = None) -> Path:
    base = Path(home) if home is not None else Path.home()
    return base / ".local" / "state" / "linfix"


def db_path(home: str | Path | None = None) -> Path:
    return state_dir(home) / "diagnostics.db"


def _connect(home: str | Path | None = None) -> sqlite3.Connection:
    path = db_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.execute(
        """
        CREATE TABLE IF NOT EXISTS runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TEXT NOT NULL,
            verdict_emoji TEXT NOT NULL DEFAULT '',
            verdict_title TEXT NOT NULL DEFAULT '',
            direct_ok INTEGER NOT NULL DEFAULT 0,
            proxy_ok INTEGER NOT NULL DEFAULT 0,
            media_kbps REAL NOT NULL DEFAULT 0,
            payload TEXT NOT NULL DEFAULT '{}'
        )
        """
    )
    return connection


def report_to_dict(report: netdiag.NetReport) -> dict[str, object]:
    """Полный отчёт в JSON-совместимый вид."""

    return {
        "dc": [
            {
                "dc_id": item.dc_id,
                "ip": item.ip,
                "port": item.port,
                "ok": item.ok,
                "latency_ms": item.latency_ms,
                "error": item.error,
            }
            for item in report.dc
        ],
        "ping": [
            {"host": item.host, "ok": item.ok, "latency_ms": item.latency_ms}
            for item in report.ping
        ],
        "dns": (
            {
                "domain": report.dns.domain,
                "system_ips": report.dns.system_ips,
                "doh_ips": report.dns.doh_ips,
                "spoofed": report.dns.spoofed,
                "error": report.dns.error,
            }
            if report.dns is not None
            else None
        ),
        "media": (
            {
                "speed_kbps": report.media.speed_kbps,
                "throttled": report.media.throttled,
                "error": report.media.error,
            }
            if report.media is not None
            else None
        ),
        "proxy": (
            {
                "ok": report.proxy.ok,
                "status": report.proxy.status,
                "latency_ms": report.proxy.latency_ms,
                "detail": report.proxy.detail,
            }
            if report.proxy is not None
            else None
        ),
        "blocks": list(report.blocks),
        "verdict": list(report.verdict),
        "recommendations": list(report.recommendations),
    }


def report_from_dict(data: dict[str, object]) -> netdiag.NetReport:
    """Восстановить отчёт из JSON (для показа сохранённого прогона)."""

    def _list(value: object) -> list:
        return value if isinstance(value, list) else []

    def _dict(value: object) -> dict:
        return value if isinstance(value, dict) else {}

    report = netdiag.NetReport()
    report.dc = [
        netdiag.DcResult(
            int(_dict(item).get("dc_id") or 0),
            str(_dict(item).get("ip") or ""),
            int(_dict(item).get("port") or 0),
            bool(_dict(item).get("ok")),
            _dict(item).get("latency_ms"),
            str(_dict(item).get("error") or ""),
        )
        for item in _list(data.get("dc"))
    ]
    report.ping = [
        netdiag.PingResult(
            str(_dict(item).get("host") or ""),
            bool(_dict(item).get("ok")),
            _dict(item).get("latency_ms"),
        )
        for item in _list(data.get("ping"))
    ]
    dns = data.get("dns")
    if isinstance(dns, dict):
        report.dns = netdiag.DnsResult(
            str(dns.get("domain") or ""),
            [str(item) for item in _list(dns.get("system_ips"))],
            [str(item) for item in _list(dns.get("doh_ips"))],
            bool(dns.get("spoofed")),
            str(dns.get("error") or ""),
        )
    media = data.get("media")
    if isinstance(media, dict):
        report.media = netdiag.MediaResult(
            float(media.get("speed_kbps") or 0.0),
            bool(media.get("throttled")),
            str(media.get("error") or ""),
        )
    proxy = data.get("proxy")
    if isinstance(proxy, dict):
        report.proxy = netdiag.proxyprobe.ProxyProbe(
            bool(proxy.get("ok")),
            str(proxy.get("status") or ""),
            int(proxy.get("latency_ms") or 0),
            str(proxy.get("detail") or ""),
        )
    report.blocks = [str(item) for item in _list(data.get("blocks"))]
    verdict = _list(data.get("verdict"))
    report.verdict = tuple(str(item) for item in verdict[:3]) if verdict else ("", "", "")
    report.recommendations = [str(item) for item in _list(data.get("recommendations"))]
    return report


def save(report: netdiag.NetReport, *, home: str | Path | None = None) -> int:
    """Сохранить прогон и вернуть его id."""

    emoji, title, _detail = report.verdict
    direct_ok = any(item.ok for item in report.dc)
    proxy_ok = bool(report.proxy and report.proxy.ok)
    media_kbps = float(report.media.speed_kbps) if report.media else 0.0

    connection = _connect(home)
    try:
        cursor = connection.execute(
            """
            INSERT INTO runs (created_at, verdict_emoji, verdict_title,
                              direct_ok, proxy_ok, media_kbps, payload)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                datetime.now().astimezone().isoformat(timespec="seconds"),
                emoji,
                title,
                int(direct_ok),
                int(proxy_ok),
                media_kbps,
                json.dumps(report_to_dict(report), ensure_ascii=False),
            ),
        )
        connection.execute(
            "DELETE FROM runs WHERE id NOT IN "
            "(SELECT id FROM runs ORDER BY id DESC LIMIT ?)",
            (KEEP_RUNS,),
        )
        connection.commit()
        return int(cursor.lastrowid or 0)
    finally:
        connection.close()


def recent(limit: int = 20, *, home: str | Path | None = None) -> list[HistoryEntry]:
    connection = _connect(home)
    try:
        rows = connection.execute(
            """
            SELECT id, created_at, verdict_emoji, verdict_title,
                   direct_ok, proxy_ok, media_kbps, payload
            FROM runs ORDER BY id DESC LIMIT ?
            """,
            (limit,),
        ).fetchall()
    finally:
        connection.close()
    return [
        HistoryEntry(
            run_id=row[0],
            created_at=row[1],
            verdict_emoji=row[2],
            verdict_title=row[3],
            direct_ok=bool(row[4]),
            proxy_ok=bool(row[5]),
            media_kbps=float(row[6]),
            payload=row[7],
        )
        for row in rows
    ]


def clear(*, home: str | Path | None = None) -> None:
    connection = _connect(home)
    try:
        connection.execute("DELETE FROM runs")
        connection.commit()
    finally:
        connection.close()
