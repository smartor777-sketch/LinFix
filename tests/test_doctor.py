from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from netfix import config, doctor


class TlsCheckTests(unittest.TestCase):
    def test_all_ok(self) -> None:
        with mock.patch.object(
            doctor.cfdomains,
            "test_domains",
            return_value=[("a", True, "TLSv1.3"), ("b", True, "TLSv1.3")],
        ):
            check = doctor._tls_check(config.Config())
        self.assertEqual(check.status, doctor.PASS)

    def test_all_fail(self) -> None:
        with mock.patch.object(
            doctor.cfdomains,
            "test_domains",
            return_value=[("a", False, "timeout"), ("b", False, "timeout")],
        ):
            check = doctor._tls_check(config.Config())
        self.assertEqual(check.status, doctor.FAIL)
        self.assertIn("timeout", check.details)
        self.assertTrue(check.hint)

    def test_partial(self) -> None:
        with mock.patch.object(
            doctor.cfdomains,
            "test_domains",
            return_value=[("a", True, "TLSv1.3"), ("b", False, "timeout")],
        ):
            check = doctor._tls_check(config.Config())
        self.assertEqual(check.status, doctor.WARN)


class TelegramCheckTests(unittest.TestCase):
    def test_established_connection(self) -> None:
        output = (
            "State Recv-Q Send-Q Local Address:Port Peer Address:Port Process\n"
            "ESTAB 0 0 127.0.0.1:1080 127.0.0.1:54321\n"
        )
        with mock.patch.object(
            doctor.util, "run", return_value=SimpleNamespace(stdout=output)
        ):
            check = doctor._telegram_check(config.Config())
        self.assertEqual(check.status, doctor.PASS)
        self.assertIn("1", check.details)

    def test_no_connections(self) -> None:
        with mock.patch.object(
            doctor.util, "run", return_value=SimpleNamespace(stdout="")
        ):
            check = doctor._telegram_check(config.Config())
        self.assertEqual(check.status, doctor.WARN)


class GuardDoctorTests(unittest.TestCase):
    def test_no_trip(self) -> None:
        with mock.patch.object(doctor.guard, "load_event", return_value={}):
            check = doctor._guard_check(config.Config())
        self.assertEqual(check.status, doctor.PASS)

    def test_last_trip(self) -> None:
        event = {"last_trip": "2026-11-20T10:00:00+03:00", "detail": "CF-путь недоступен"}
        with mock.patch.object(doctor.guard, "load_event", return_value=event):
            check = doctor._guard_check(config.Config())
        self.assertEqual(check.status, doctor.WARN)
        self.assertIn("CF-путь", check.details)

    def test_disabled(self) -> None:
        cfg = config.Config(guard=config.GuardConfig(enabled=False))
        check = doctor._guard_check(cfg)
        self.assertEqual(check.status, doctor.WARN)
        self.assertEqual(check.details, "выключен")


if __name__ == "__main__":
    unittest.main()
