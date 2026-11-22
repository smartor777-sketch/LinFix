from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from linfix import guard, tgws
from linfix.config import Config, GuardConfig

STALL_LINE = (
    "2026-11-22 01:45:09  DEBUG  tg-mtproto-proxy  H2 DETAIL CLOSE lane=1 "
    "[channel=18 req=11 pkt=1 sid=23 replay=0 phase=headers age_ms=7998 "
    "send_ms=1 wait_headers_ms=7997 up=180 down=0 ray=- attempts=- "
    "recovery=not-retained recent=[]]"
)
FAIL_LINE = (
    "2026-11-22 01:45:09  WARNING  tg-mtproto-proxy  [127.0.0.1:1] "
    "H2 lane=1 channel=18 request failed: ReadTimeout: timed out; detail=[-]; "
    "closing native channel, reconnect may select another domain"
)


def make_cfg(enabled: bool = True, threshold: int = 20) -> Config:
    return Config(guard=GuardConfig(enabled=enabled, error_threshold=threshold))


class H2FallbackTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.log = self.home / ".config" / "TgWsProxy" / "proxy.log"
        self.log.parent.mkdir(parents=True)
        self.config = self.home / ".config" / "TgWsProxy" / "config.json"
        self.config.write_text(
            json.dumps({"h2": True, "cfproxy_user_domain": ["pclead.co.uk"]}),
            encoding="utf-8",
        )

    def _run(self, lines: list[str]):
        self.log.write_text("\n".join(lines) + "\n", encoding="utf-8")
        with mock.patch.object(guard.util, "run", return_value=mock.Mock(returncode=0)) as run:
            report = guard.check_guard(
                make_cfg(), home=self.home, skip_history=False, h2_probe=lambda: False
            )
        return report, run

    def test_counts_stalls(self) -> None:
        self.assertEqual(guard.count_h2_stalls([STALL_LINE] * 3), 3)
        self.assertEqual(guard.count_h2_stalls([FAIL_LINE] * 2), 0)

    def test_ignores_headers_with_data(self) -> None:
        line = STALL_LINE.replace("down=0", "down=4096")
        self.assertEqual(guard.count_h2_stalls([line]), 0)

    def test_should_disable_only_when_serious(self) -> None:
        self.assertFalse(guard.should_disable_h2(1, True))
        self.assertFalse(guard.should_disable_h2(5, False))
        self.assertTrue(guard.should_disable_h2(5, True))
        self.assertTrue(guard.should_disable_h2(0, True, failures=5))

    def test_disables_h2_and_restarts(self) -> None:
        report, run = self._run([STALL_LINE] * guard.H2_STALL_MIN)
        self.assertEqual(report.status, "fallback")
        self.assertTrue(report.h2_disabled)
        data = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(data["h2"], False)
        calls = [call.args[0] for call in run.call_args_list]
        self.assertIn(["systemctl", "--user", "restart", tgws.UNIT_NAME], calls)

    def _restart_calls(self, run) -> list:
        """Только вызовы restart: is-active/ss делают другие проверки."""

        return [
            call_ for call_ in run.call_args_list
            if call_.args and "restart" in call_.args[0]
        ]

    def test_no_fallback_when_h2_already_off(self) -> None:
        self.config.write_text(json.dumps({"h2": False}), encoding="utf-8")
        report, run = self._run([STALL_LINE] * guard.H2_STALL_MIN)
        self.assertEqual(report.status, "ok")
        self.assertEqual(self._restart_calls(run), [])

    def test_no_fallback_below_threshold(self) -> None:
        report, run = self._run([STALL_LINE] * (guard.H2_STALL_MIN - 1))
        self.assertEqual(report.status, "ok")
        self.assertEqual(self._restart_calls(run), [])

    def test_first_run_skips_history(self) -> None:
        """При первом запуске старые зависания в логе не должны отключать H2."""

        self.log.write_text("\n".join([STALL_LINE] * 50) + "\n", encoding="utf-8")
        with mock.patch.object(guard.util, "run", return_value=mock.Mock(returncode=0)) as run:
            report = guard.check_guard(make_cfg(), home=self.home)
        self.assertEqual(report.status, "ok")
        self.assertFalse(report.h2_disabled)
        restarts = [
            call_ for call_ in run.call_args_list
            if call_.args and "restart" in call_.args[0]
        ]
        self.assertEqual(restarts, [])
        data = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(data["h2"], True)

    def test_falls_back_on_timeout_series(self) -> None:
        report, _ = self._run([FAIL_LINE] * guard.H2_STALL_MIN)
        self.assertEqual(report.status, "fallback")
        self.assertTrue(report.h2_disabled)

    def test_does_not_disable_h2_when_live_probe_passes(self) -> None:
        """История лога не должна отключать H2, если живой запрос проходит."""

        self.log.write_text("\n".join([STALL_LINE] * 20) + "\n", encoding="utf-8")
        with mock.patch.object(guard.util, "run", return_value=mock.Mock(returncode=0)) as run:
            report = guard.check_guard(
                make_cfg(),
                home=self.home,
                skip_history=False,
                h2_probe=lambda: True,
            )
        self.assertEqual(report.status, "warn")
        self.assertFalse(report.h2_disabled)
        self.assertIn("живой запрос проходит", report.detail)
        restarts = [
            call_ for call_ in run.call_args_list
            if call_.args and "restart" in call_.args[0]
        ]
        self.assertEqual(restarts, [])
        data = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(data["h2"], True)

    def test_event_written(self) -> None:
        self._run([STALL_LINE] * guard.H2_STALL_MIN)
        event = guard.load_event(self.home)
        self.assertEqual(event["status"], "fallback")
        self.assertTrue(event["h2_disabled"])
        self.assertEqual(event["h2_config"], False)


if __name__ == "__main__":
    unittest.main()
