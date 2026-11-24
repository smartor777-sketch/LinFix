"""Тесты сетевой диагностики: классификация, вердикт, рекомендации."""

from __future__ import annotations

import unittest
from unittest import mock

from linfix import blockcheck, netdiag
from pathlib import Path


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
        )
        self.assertIn(netdiag.BLOCK_IP, netdiag.classify(report))

    def test_no_sni_classification_anymore(self) -> None:
        """SNI/DPI-разбор перенесён в «Сайты», здесь его быть не должно."""

        self.assertFalse(hasattr(netdiag, "DpiResult"))
        self.assertFalse(hasattr(netdiag, "BLOCK_SNI"))
        report = _report(
            ping=[netdiag.PingResult("1.1.1.1", True, 20)],
            dc=[netdiag.DcResult(1, "1.1.1.1", 443, False)],
        )
        self.assertEqual(netdiag.classify(report), [netdiag.BLOCK_IP])

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
        # Колонки: проверка, напрямую, через прокси, детали.
        self.assertEqual(rows[0][1], "OK")
        self.assertEqual(rows[0][2], "BLOCK")

    def test_dns_spoof_row(self) -> None:
        report = _report(
            dns=netdiag.DnsResult("telegram.org", ["10.0.0.1"], ["149.1.1.1"], True)
        )
        rows = report.as_rows()
        self.assertEqual(rows[0][1], "BLOCK")
        self.assertIn("подменён", rows[0][3])

    def test_proxy_ok_marks_proxy_column(self) -> None:
        report = _report(
            dc=[netdiag.DcResult(2, "149.154.167.51", 443, False)],
            proxy=netdiag.proxyprobe.ProxyProbe(True, "ok", 300, "resPQ получен"),
        )
        rows = report.as_rows()
        dc_row = next(row for row in rows if row[0].startswith("DC2"))
        self.assertEqual(dc_row[1], "BLOCK")
        self.assertIn("OK", dc_row[2])

    def test_proxy_row_present(self) -> None:
        report = _report(
            proxy=netdiag.proxyprobe.ProxyProbe(False, "timeout", 0, "нет ответа"),
        )
        rows = report.as_rows()
        row = rows[-1]
        self.assertIn("TgWsProxy", row[0])
        self.assertEqual(row[2], "BLOCK")


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

    def test_normalize_strips_prefixes(self) -> None:
        from linfix import blockcheck

        raw = "curl_test_https_tls12 ipv4 rutracker.org : nfqws2 --filter-tcp=443 --lua-desync=multisplit:pos=1"
        self.assertEqual(
            blockcheck.normalize_args(raw),
            "--filter-tcp=443 --lua-desync=multisplit:pos=1",
        )
        self.assertEqual(blockcheck.normalize_args("nfqws2 --filter-tcp=443"), "--filter-tcp=443")
        self.assertEqual(blockcheck.normalize_args("curl_test... nfqws2 not working"), "")

    def test_parse_coverage_lines_real_output(self) -> None:
        from linfix import blockcheck

        text = (
            "* COVERAGE\n"
            "2/2 : curl_test_https_tls12 ipv4 : nfqws2 --filter-tcp=443 --lua-desync=multisplit:pos=1\n"
            "1/2 : curl_test_https_tls13 ipv4 : tpws --hostlist=x --lua-desync=fake:blob=fake_default_tls\n"
            "0/2 : curl_test_http_basic ipv4 : nfqws2 not working\n"
        )
        strategies = blockcheck.parse_output(text)
        self.assertEqual(
            [s.args for s in strategies],
            [
                "--filter-tcp=443 --lua-desync=multisplit:pos=1",
                "--hostlist=x --lua-desync=fake:blob=fake_default_tls",
            ],
        )
        self.assertEqual(strategies[0].success, 2)

    def test_parse_found_report_lines(self) -> None:
        from linfix import blockcheck

        text = (
            "!!!!! curl_test_https_tls12: working strategy found for ipv4 rutracker.org : "
            "nfqws2 --filter-tcp=443 --lua-desync=tcpseg:pos=0,1:ip_id=rnd !!!!\n"
            "!!!!! curl_test_https_tls12 ipv4 rutracker.org : nfqws2 not working\n"
        )
        strategies = blockcheck.parse_output(text)
        self.assertEqual(len(strategies), 1)
        self.assertEqual(strategies[0].args, "--filter-tcp=443 --lua-desync=tcpseg:pos=0,1:ip_id=rnd")

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
        with mock.patch.object(blockcheck, "running", return_value={}), mock.patch(
            "linfix.zapret.detect_zapret", return_value=active
        ), mock.patch.object(blockcheck, "stray_processes", return_value=[]), mock.patch.object(
            blockcheck, "script_path", return_value=__import__("pathlib").Path("/tmp")
        ), mock.patch("pathlib.Path.is_file", return_value=True):
            ok, reason = blockcheck.check_readiness(cfg)
        # Работающий zapret не мешает: helper сам остановит его на время
        # замеров и вернёт обратно. Раньше здесь было ложное «не готово».
        self.assertTrue(ok)
        self.assertIn("остановлен", reason)

    def test_readiness_reports_busy_scan(self) -> None:
        from types import SimpleNamespace
        from linfix import blockcheck

        cfg = SimpleNamespace(zapret=SimpleNamespace(service="zapret2"))
        with mock.patch.object(blockcheck, "running", return_value={"pid": 123}):
            ok, reason = blockcheck.check_readiness(cfg)
        self.assertFalse(ok)
        self.assertIn("уже идёт", reason)


class BlockcheckBypassTests(unittest.TestCase):
    def test_detects_working_without_bypass(self) -> None:
        import tempfile
        from pathlib import Path

        from linfix import blockcheck

        with tempfile.TemporaryDirectory() as tmp:
            # blockcheck2 не заглушка: проверяем реальный запуск скрипта.
            script = Path(tmp) / "blockcheck2.sh"
            script.write_text(
                'echo "* SUMMARY"\n'
                'echo "curl_test_https_tls12 ipv4 rutracker.org : working without bypass"\n',
                encoding="utf-8",
            )
            result = blockcheck.run_blockcheck(tmp, domains=["rutracker.org"], timeout=30)
        self.assertTrue(result.works_without_bypass)
        self.assertEqual(result.strategies, [])


class ParallelTests(unittest.TestCase):
    def test_repeats_enable_parallel(self) -> None:
        """PARALLEL в blockcheck2 работает только при REPEATS > 1."""

        from linfix import blockcheck

        env = blockcheck.build_env("/opt/zapret2", ["a.org"], "quick")
        self.assertEqual(env["REPEATS"], "2")
        self.assertEqual(env["PARALLEL"], "1")

    def test_single_repeat_disables_parallel(self) -> None:
        from linfix import blockcheck

        env = blockcheck.build_env("/opt/zapret2", ["a.org"], "quick", repeats=1)
        self.assertEqual(env["REPEATS"], "1")
        self.assertEqual(env["PARALLEL"], "0")

    def test_zero_repeats_clamped(self) -> None:
        from linfix import blockcheck

        env = blockcheck.build_env("/opt/zapret2", ["a.org"], "quick", repeats=0)
        self.assertEqual(env["REPEATS"], "1")


class FastSkipTests(unittest.TestCase):
    """Быстрый режим отключает заведомо неприменимые семейства."""

    def test_fast_sets_notest_flags(self) -> None:
        env = blockcheck.build_env("/opt/zapret2", ["a.org"], "quick", fast=True)
        self.assertEqual(env["ENABLE_HTTP"], "0", "HTTP-ветки не нужны для CF")
        for name in blockcheck.FAST_SKIP:
            self.assertEqual(env.get(f"NOTEST_{name}"), "1", name)

    def test_full_mode_has_no_notest(self) -> None:
        env = blockcheck.build_env("/opt/zapret2", ["a.org"], "quick", fast=False)
        self.assertEqual(env["ENABLE_HTTP"], "1")
        self.assertEqual([k for k in env if k.startswith("NOTEST_")], [])

    def test_keeps_tls_fake_family(self) -> None:
        """fake, multisplit, seqovl и прочие рабочие семейства не отключены."""

        env = blockcheck.build_env("/opt/zapret2", ["a.org"], "quick", fast=True)
        for name in ("FAKE_HTTPS", "FAKE_MULTI_HTTPS", "FAKE_FAKED_HTTPS"):
            self.assertNotIn(f"NOTEST_{name}", env)

    def test_run_blockcheck_passes_fast(self) -> None:
        calls: dict[str, object] = {}

        def fake_build_env(install_dir, domains, level, **kwargs):
            calls.update(kwargs)
            return {}

        with mock.patch.object(blockcheck, "build_env", side_effect=fake_build_env):
            with mock.patch.object(blockcheck, "script_path") as sp:
                sp.return_value = Path(__file__)
                try:
                    blockcheck.run_blockcheck(
                        "/opt/zapret2", domains=["a.org"], timeout=1, fast=False
                    )
                except Exception:
                    pass
        self.assertEqual(calls.get("fast"), False)
