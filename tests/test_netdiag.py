"""Тесты сетевой диагностики: классификация, вердикт, рекомендации."""

from __future__ import annotations

import unittest
from unittest import mock

from linfix import netdiag


def _report(**kwargs) -> netdiag.NetReport:
    report = netdiag.NetReport()
    for key, value in kwargs.items():
        setattr(report, key, value)
    return report


class ClassifyTests(unittest.TestCase):
    def test_no_internet(self) -> None:
        report = _report(
            ping=[netdiag.PingResult("1.1.1.1", False)],
            dc=[netdiag.DcResult(1, "1.1.1.1", 443, False)],
        )
        self.assertEqual(netdiag.classify(report), [netdiag.BLOCK_NO_INTERNET])

    def test_ip_block(self) -> None:
        report = _report(
            ping=[netdiag.PingResult("1.1.1.1", True, 20)],
            dc=[netdiag.DcResult(1, "1.1.1.1", 443, False)],
            dpi=netdiag.DpiResult("timeout", "timeout", False),
        )
        self.assertIn(netdiag.BLOCK_IP, netdiag.classify(report))

    def test_sni_block(self) -> None:
        report = _report(
            ping=[netdiag.PingResult("1.1.1.1", True, 20)],
            dc=[netdiag.DcResult(1, "1.1.1.1", 443, False)],
            dpi=netdiag.DpiResult("timeout", "ok", True),
        )
        self.assertIn(netdiag.BLOCK_SNI, netdiag.classify(report))

    def test_dns_spoof(self) -> None:
        dns = netdiag.DnsResult("telegram.org", ["10.0.0.1"], ["149.154.167.51"], True)
        report = _report(
            ping=[netdiag.PingResult("1.1.1.1", True, 20)],
            dc=[netdiag.DcResult(1, "1.1.1.1", 443, True, 50)],
            dns=dns,
        )
        self.assertIn(netdiag.BLOCK_DNS, netdiag.classify(report))

    def test_throttling(self) -> None:
        report = _report(
            ping=[netdiag.PingResult("1.1.1.1", True, 30)],
            dc=[netdiag.DcResult(1, "1.1.1.1", 443, True, 400)],
        )
        self.assertIn(netdiag.BLOCK_THROTTLE, netdiag.classify(report))

    def test_media_throttle(self) -> None:
        report = _report(
            ping=[netdiag.PingResult("1.1.1.1", True, 20)],
            dc=[netdiag.DcResult(1, "1.1.1.1", 443, True, 40)],
            media=netdiag.MediaResult(50.0, True),
        )
        self.assertIn(netdiag.BLOCK_MEDIA, netdiag.classify(report))

    def test_clean(self) -> None:
        report = _report(
            ping=[netdiag.PingResult("1.1.1.1", True, 20)],
            dc=[netdiag.DcResult(1, "1.1.1.1", 443, True, 40)],
            dpi=netdiag.DpiResult("ok", "ok", False),
            dns=netdiag.DnsResult("x", ["1.1.1.1"], ["1.1.1.1"], False),
        )
        self.assertEqual(netdiag.classify(report), [])


class VerdictTests(unittest.TestCase):
    def test_proxy_running_wins(self) -> None:
        report = _report(blocks=[netdiag.BLOCK_IP])
        emoji, title, _detail = netdiag.verdict(report, proxy_running=True, zapret_running=True)
        self.assertEqual(emoji, "🟢")
        self.assertIn("TgWsProxy", title)

    def test_no_internet_is_red(self) -> None:
        report = _report(blocks=[netdiag.BLOCK_NO_INTERNET])
        emoji, title, _ = netdiag.verdict(report, proxy_running=False, zapret_running=False)
        self.assertEqual(emoji, "🔴")
        self.assertIn("Интернета", title)

    def test_sni_with_zapret_is_green(self) -> None:
        report = _report(blocks=[netdiag.BLOCK_SNI])
        emoji, _title, _ = netdiag.verdict(report, proxy_running=False, zapret_running=True)
        self.assertEqual(emoji, "🟢")

    def test_sni_without_zapret_is_red(self) -> None:
        report = _report(blocks=[netdiag.BLOCK_SNI])
        emoji, _title, _ = netdiag.verdict(report, proxy_running=False, zapret_running=False)
        self.assertEqual(emoji, "🔴")


class RecommendationsTests(unittest.TestCase):
    def test_always_has_tip(self) -> None:
        self.assertTrue(netdiag.recommendations(_report(blocks=[]), zapret_running=True))

    def test_ip_block_mentions_proxy(self) -> None:
        tips = netdiag.recommendations(
            _report(blocks=[netdiag.BLOCK_IP]), zapret_running=True
        )
        self.assertTrue(any("TgWsProxy" in tip for tip in tips))


class RowsTests(unittest.TestCase):
    def test_rows_include_dc_and_ping(self) -> None:
        report = _report(
            dc=[netdiag.DcResult(1, "1.1.1.1", 443, True, 42.0)],
            ping=[netdiag.PingResult("1.1.1.1", True, 20.0)],
        )
        rows = report.as_rows()
        self.assertEqual(len(rows), 2)
        self.assertIn("DC1", rows[0][0])
        self.assertEqual(rows[0][1], "ok")

    def test_dns_spoof_row(self) -> None:
        report = _report(
            dns=netdiag.DnsResult("telegram.org", ["10.0.0.1"], ["149.1.1.1"], True)
        )
        rows = report.as_rows()
        self.assertEqual(rows[0][1], "fail")
        self.assertIn("подменён", rows[0][2])


class SpeedTests(unittest.TestCase):
    def test_dc_checks_run_in_parallel(self) -> None:
        """15 TCP-проверок не должны занимать минуту."""

        import time

        def slow_connect(ip, port, timeout=3.0):
            time.sleep(0.15)
            return False, 0.0

        with mock.patch.object(netdiag, "tcp_connect", side_effect=slow_connect):
            started = time.monotonic()
            netdiag.check_dc(1.0)
            elapsed = time.monotonic() - started
        # последовательно было бы 15 * 0.15 = 2.25 с
        self.assertLess(elapsed, 1.5)


if __name__ == "__main__":
    unittest.main()


class BlockcheckTests(unittest.TestCase):
    SAMPLE = (
        "2/2 : --filter-tcp=443 --hostlist=x --lua-desync=multisplit:pos=1\n"
        "1/2 : --filter-tcp=443 --lua-desync=fake:blob=fake_default_tls\n"
        "комментарий без формата\n"
        "1/2 : --filter-tcp=443 --lua-desync=fake:blob=fake_default_tls\n"
    )

    def test_parse_sorts_and_dedups(self) -> None:
        from linfix import blockcheck

        strategies = blockcheck.parse_output(self.SAMPLE)
        self.assertEqual(len(strategies), 2)
        self.assertEqual(strategies[0].score, 1.0)
        self.assertIn("multisplit", strategies[0].args)

    def test_desync_only(self) -> None:
        from linfix import blockcheck

        best = blockcheck.parse_output(self.SAMPLE)[0]
        self.assertEqual(best.desync, ["--lua-desync=multisplit:pos=1"])

    def test_build_env_is_noninteractive(self) -> None:
        from linfix import blockcheck

        env = blockcheck.build_env("/opt/zapret2", ["a.org", "b.org"], "quick")
        self.assertEqual(env["DOMAINS"], "a.org b.org")
        self.assertEqual(env["SCANLEVEL"], "quick")
        self.assertEqual(env["BATCH"], "1")
        self.assertEqual(env["IPVS"], "4")

    def test_bad_level_rejected(self) -> None:
        from linfix import blockcheck

        with self.assertRaises(blockcheck.BlockcheckError):
            blockcheck.run_blockcheck("/opt/zapret2", domains=["a"], level="turbo")

    def test_missing_script(self) -> None:
        from linfix import blockcheck

        with self.assertRaises(blockcheck.BlockcheckError):
            blockcheck.run_blockcheck("/nonexistent", domains=["a"])

    def test_readiness_requires_stopped_zapret(self) -> None:
        from types import SimpleNamespace
        from linfix import blockcheck

        cfg = SimpleNamespace(zapret=SimpleNamespace(service="zapret2"))
        active = SimpleNamespace(install_dir="/opt/zapret2", active=True)
        with mock.patch("linfix.zapret.detect_zapret", return_value=active), mock.patch.object(
            blockcheck, "script_path", return_value=__import__("pathlib").Path("/tmp")
        ), mock.patch("pathlib.Path.is_file", return_value=True):
            ok, reason = blockcheck.check_readiness(cfg)
        self.assertFalse(ok)
        self.assertIn("остановить", reason)
