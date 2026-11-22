from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from linfix import config, guard
from linfix.config import GuardConfig

FAIL_LINE = "2026-11-20 10:00:00 CF proxy failed for DC2"


def make_cfg(enabled: bool = True, threshold: int = 20) -> config.Config:
    return config.Config(guard=GuardConfig(enabled=enabled, error_threshold=threshold))


class CursorTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.log = self.root / "proxy.log"

    def test_reads_only_new_lines(self) -> None:
        self.log.write_text("one\ntwo\n", encoding="utf-8")
        lines, cursor = guard.read_new_lines(self.log, None)
        self.assertEqual(lines, ["one", "two"])

        self.log.write_text("one\ntwo\nthree\n", encoding="utf-8")
        lines, cursor = guard.read_new_lines(self.log, cursor)
        self.assertEqual(lines, ["three"])

    def test_truncate_resets_offset(self) -> None:
        self.log.write_text("a\nb\nc\n", encoding="utf-8")
        _, cursor = guard.read_new_lines(self.log, None)
        self.log.write_text("x\n", encoding="utf-8")
        lines, cursor = guard.read_new_lines(self.log, cursor)
        self.assertEqual(lines, ["x"])

    def test_cursor_roundtrip(self) -> None:
        path = self.root / "guard.json"
        cursor = guard.Cursor(path="/x", offset=42, inode=7)
        guard.save_cursor(path, cursor)
        self.assertEqual(guard.load_cursor(path), cursor)
        self.assertEqual(guard.load_cursor(self.root / "missing"), None)


class CheckGuardTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.log = self.home / ".config" / "TgWsProxy" / "proxy.log"
        self.log.parent.mkdir(parents=True)
        self.stopped: list[bool] = []

    def stop(self):
        self.stopped.append(True)
        return SimpleNamespace(returncode=0)

    def test_below_threshold(self) -> None:
        self.log.write_text("\n".join([FAIL_LINE] * 19) + "\n", encoding="utf-8")
        report = guard.check_guard(
            make_cfg(),
            home=self.home,
            skip_history=False,
            tls_test=lambda: [("kws2.x", False, "timeout")],
            stop_service=self.stop,
        )
        self.assertEqual(report.status, "ok")
        self.assertFalse(report.tripped)
        self.assertEqual(self.stopped, [])

    def test_trip_when_tls_fails(self) -> None:
        self.log.write_text("\n".join([FAIL_LINE] * 21) + "\n", encoding="utf-8")
        report = guard.check_guard(
            make_cfg(),
            home=self.home,
            skip_history=False,
            tls_test=lambda: [("kws2.x", False, "timeout")],
            stop_service=self.stop,
        )
        self.assertEqual(report.status, "tripped")
        self.assertTrue(report.tripped)
        self.assertTrue(report.stopped)
        self.assertFalse(report.tls_ok)
        self.assertEqual(self.stopped, [True])

        event = guard.load_event(self.home)
        self.assertEqual(event["status"], "tripped")
        self.assertIn("CF-путь недоступен", str(event["detail"]))

    def test_no_stop_when_tls_ok(self) -> None:
        self.log.write_text("\n".join([FAIL_LINE] * 21) + "\n", encoding="utf-8")
        report = guard.check_guard(
            make_cfg(),
            home=self.home,
            skip_history=False,
            tls_test=lambda: [("kws2.x", True, "TLSv1.3")],
            stop_service=self.stop,
        )
        self.assertEqual(report.status, "warn")
        self.assertFalse(report.tripped)
        self.assertEqual(self.stopped, [])

    def test_disabled(self) -> None:
        report = guard.check_guard(make_cfg(enabled=False), home=self.home)
        self.assertEqual(report.status, "disabled")

    def test_missing_log(self) -> None:
        report = guard.check_guard(make_cfg(), home=self.home / "empty")
        self.assertEqual(report.status, "warn")

    def test_incremental_no_double_count(self) -> None:
        self.log.write_text("\n".join([FAIL_LINE] * 21) + "\n", encoding="utf-8")
        guard.check_guard(
            make_cfg(),
            home=self.home,
            tls_test=lambda: [("kws2.x", False, "timeout")],
            stop_service=self.stop,
        )
        report = guard.check_guard(
            make_cfg(),
            home=self.home,
            skip_history=False,
            tls_test=lambda: [("kws2.x", False, "timeout")],
            stop_service=self.stop,
        )
        self.assertEqual(report.failures, 0)
        self.assertEqual(report.status, "ok")


class SetGuardEnabledTests(unittest.TestCase):
    def test_replace_in_existing_section(self) -> None:
        text = "[general]\nlog_level = \"info\"\n\n[guard]\n# keep comment\nenabled = false\nwindow_seconds = 60\n"
        result = config.set_guard_enabled(text, True)
        self.assertIn("# keep comment", result)
        self.assertIn("enabled = true", result)
        self.assertIn("window_seconds = 60", result)
        self.assertIn("log_level = \"info\"", result)
        self.assertNotIn("enabled = false", result)

    def test_insert_when_key_missing(self) -> None:
        text = "[guard]\nwindow_seconds = 30\n"
        result = config.set_guard_enabled(text, False)
        self.assertIn("[guard]\nenabled = false\nwindow_seconds = 30", result)

    def test_append_section(self) -> None:
        text = "[general]\nlog_level = \"info\"\n"
        result = config.set_guard_enabled(text, True)
        self.assertIn("[general]", result)
        self.assertIn("[guard]", result)
        self.assertIn("enabled = true", result)

    def test_crlf_preserved(self) -> None:
        text = "[guard]\r\nenabled = false\r\n"
        result = config.set_guard_enabled(text, True)
        self.assertIn("enabled = true\r\n", result)
        self.assertNotIn("\n", result.replace("\r\n", ""))

    def test_idempotent(self) -> None:
        text = "[guard]\nenabled = true\n"
        self.assertEqual(config.set_guard_enabled(text, True), text)


if __name__ == "__main__":
    unittest.main()


class TimerTests(unittest.TestCase):
    def test_ensure_timer_enables_and_starts(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            calls.append(list(cmd))
            return type("CP", (), {"returncode": 0, "stdout": "enabled", "stderr": ""})()

        with mock.patch.object(guard.util, "run", side_effect=fake_run):
            result = guard.ensure_timer(True)

        self.assertTrue(result["timer_ok"])
        self.assertEqual(result["timer_state"], "enabled")
        self.assertIn(["systemctl", "--user", "start", "linfix-guard.timer"], calls)
        self.assertIn(["systemctl", "--user", "enable", "linfix-guard.timer"], calls)

    def test_ensure_timer_disables_and_stops(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            calls.append(list(cmd))
            return type("CP", (), {"returncode": 0, "stdout": "disabled", "stderr": ""})()

        with mock.patch.object(guard.util, "run", side_effect=fake_run):
            guard.ensure_timer(False)

        self.assertIn(["systemctl", "--user", "stop", "linfix-guard.timer"], calls)
        self.assertIn(["systemctl", "--user", "disable", "linfix-guard.timer"], calls)
