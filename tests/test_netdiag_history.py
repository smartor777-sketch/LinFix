from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from linfix import netdiag, netdiag_history


def make_report(*, proxy_ok: bool = True, direct_ok: bool = False) -> netdiag.NetReport:
    report = netdiag.NetReport()
    report.dc = [
        netdiag.DcResult(2, "149.154.167.51", 443, direct_ok, 50.0 if direct_ok else None,
                         "" if direct_ok else "timeout"),
    ]
    report.proxy = netdiag.proxyprobe.ProxyProbe(
        proxy_ok, "ok" if proxy_ok else "timeout", 400 if proxy_ok else 0,
        "resPQ получен" if proxy_ok else "нет ответа",
    )
    report.media = netdiag.MediaResult(820.0, False)
    report.verdict = ("🟢" if proxy_ok else "🔴", "Вердикт", "Детали")
    report.recommendations = ["Совет"]
    return report


class HistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)

    def test_save_and_recent(self) -> None:
        run_id = netdiag_history.save(make_report(), home=self.home)
        self.assertGreater(run_id, 0)
        entries = netdiag_history.recent(home=self.home)
        self.assertEqual(len(entries), 1)
        self.assertEqual(entries[0].verdict_title, "Вердикт")
        self.assertTrue(entries[0].proxy_ok)
        self.assertFalse(entries[0].direct_ok)
        self.assertEqual(entries[0].media_kbps, 820.0)

    def test_newest_first(self) -> None:
        netdiag_history.save(make_report(proxy_ok=True), home=self.home)
        netdiag_history.save(make_report(proxy_ok=False), home=self.home)
        entries = netdiag_history.recent(home=self.home)
        self.assertEqual(len(entries), 2)
        self.assertFalse(entries[0].proxy_ok)  # последний — сверху

    def test_payload_roundtrip(self) -> None:
        netdiag_history.save(make_report(), home=self.home)
        entry = netdiag_history.recent(home=self.home)[0]
        restored = entry.report()
        self.assertIsNotNone(restored)
        self.assertEqual(len(restored.dc), 1)
        self.assertTrue(restored.proxy.ok)
        self.assertEqual(restored.media.speed_kbps, 820.0)
        self.assertEqual(restored.recommendations, ["Совет"])

    def test_clear(self) -> None:
        netdiag_history.save(make_report(), home=self.home)
        netdiag_history.clear(home=self.home)
        self.assertEqual(netdiag_history.recent(home=self.home), [])

    def test_limit_and_trim(self) -> None:
        for _ in range(5):
            netdiag_history.save(make_report(), home=self.home)
        self.assertEqual(len(netdiag_history.recent(limit=3, home=self.home)), 3)

    def test_created_display_format(self) -> None:
        entry = netdiag_history.HistoryEntry(
            run_id=1,
            created_at="2026-11-23T02:15:30+03:00",
            verdict_emoji="🟢",
            verdict_title="ОК",
            direct_ok=False,
            proxy_ok=True,
            media_kbps=100.0,
            payload="{}",
        )
        self.assertEqual(entry.created_display, "23.11 02:15")

    def test_nonexistent_db_is_empty(self) -> None:
        self.assertEqual(netdiag_history.recent(home=self.home / "empty"), [])


if __name__ == "__main__":
    unittest.main()
