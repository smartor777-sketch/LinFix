from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from linfix import config, doctor


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

class Ipv6BlackholeTests(unittest.TestCase):
    def test_clean(self) -> None:
        def fake_run(argv, **kwargs):
            if argv[:3] == ["ip", "-6", "route"]:
                return SimpleNamespace(stdout="fe80::/64 dev wlo1 proto kernel\n")
            return SimpleNamespace(stdout="lo UNKNOWN\nwlo1 UP\n")

        with mock.patch.object(doctor.util, "run", side_effect=fake_run):
            check = doctor._ipv6_blackhole_check()
        self.assertEqual(check.status, doctor.PASS)

    def test_blackholes_detected(self) -> None:
        routes = (
            "blackhole ::/1 dev lo metric 1024\n"
            "blackhole 8000::/1 dev lo metric 1024\n"
            "blackhole 2000::/4 dev lo metric 1024\n"
        )

        def fake_run(argv, **kwargs):
            if argv[:3] == ["ip", "-6", "route"]:
                return SimpleNamespace(stdout=routes)
            return SimpleNamespace(stdout="lo UNKNOWN\nwlo1 UP\n")

        with mock.patch.object(doctor.util, "run", side_effect=fake_run):
            check = doctor._ipv6_blackhole_check()
        self.assertEqual(check.status, doctor.WARN)
        self.assertIn("3", check.details)
        self.assertIn("Убрать заглушки", check.hint)

    def test_blackholes_with_active_vpn(self) -> None:
        routes = "blackhole 2000::/4 dev lo metric 1024\n"

        def fake_run(argv, **kwargs):
            if argv[:3] == ["ip", "-6", "route"]:
                return SimpleNamespace(stdout=routes)
            return SimpleNamespace(stdout="lo UNKNOWN\nvpn0 UP\n")

        with mock.patch.object(doctor.util, "run", side_effect=fake_run):
            check = doctor._ipv6_blackhole_check()
        self.assertEqual(check.status, doctor.WARN)
        self.assertIn("VPN ещё работает", check.hint)

    def test_foreign_blackhole_hint(self) -> None:
        routes = "blackhole 2606:4700::/32 dev lo metric 1024\n"

        def fake_run(argv, **kwargs):
            if argv[:3] == ["ip", "-6", "route"]:
                return SimpleNamespace(stdout=routes)
            return SimpleNamespace(stdout="lo UNKNOWN\nwlo1 UP\n")

        with mock.patch.object(doctor.util, "run", side_effect=fake_run):
            check = doctor._ipv6_blackhole_check()
        self.assertEqual(check.status, doctor.WARN)
        self.assertNotIn("VPN", check.hint)



class Ipv6HelpersTests(unittest.TestCase):
    def test_routes_parsed(self) -> None:
        out = (
            "blackhole ::/1 dev lo metric 1024\n"
            "fe80::/64 dev wlo1 proto kernel\n"
            "blackhole 2000::/4 dev lo metric 1024\n"
        )
        with mock.patch.object(
            doctor.util, "run", return_value=SimpleNamespace(stdout=out)
        ):
            routes = doctor.ipv6_blackhole_routes()
        self.assertEqual(len(routes), 2)
        self.assertTrue(routes[0].startswith("blackhole"))

    def test_clear_removes_all(self) -> None:
        calls: list[list[str]] = []
        state = {"removed": 0}

        def fake_run(argv, **kwargs):
            calls.append(argv)
            if argv[:3] == ["ip", "-6", "route"] and argv[3] == "del":
                state["removed"] += 1
                return SimpleNamespace(returncode=0, stdout="")
            return SimpleNamespace(returncode=0, stdout="")

        with mock.patch.object(doctor.util, "run", side_effect=fake_run):
            removed, left = doctor.clear_ipv6_blackholes()
        self.assertEqual(removed, len(doctor.IPV6_BLACKHOLE_ROUTES))
        self.assertEqual(left, [])
        self.assertEqual(state["removed"], len(doctor.IPV6_BLACKHOLE_ROUTES))

    def test_vpn_process_detected(self) -> None:
        def fake_run(argv, **kwargs):
            if argv[:3] == ["ip", "-brief", "link"]:
                return SimpleNamespace(returncode=0, stdout="lo UNKNOWN\nwlo1 UP\n")
            # pgrep: qeli найден
            if argv[0] == "pgrep" and argv[-1] == "qeli":
                return SimpleNamespace(returncode=0, stdout="1234\n")
            return SimpleNamespace(returncode=1, stdout="")

        with mock.patch.object(doctor.util, "run", side_effect=fake_run):
            found = doctor.vpn_interfaces()
        self.assertIn("qeli", found)

    def test_vpn_active_blocks_removal_hint(self) -> None:
        routes = "blackhole 2000::/4 dev lo metric 1024\n"

        def fake_run(argv, **kwargs):
            if argv[:3] == ["ip", "-6", "route"]:
                return SimpleNamespace(returncode=0, stdout=routes)
            if argv[:3] == ["ip", "-brief", "link"]:
                return SimpleNamespace(returncode=0, stdout="lo UNKNOWN\nvpn0 UP\n")
            return SimpleNamespace(returncode=1, stdout="")

        with mock.patch.object(doctor.util, "run", side_effect=fake_run):
            check = doctor._ipv6_blackhole_check()
        self.assertEqual(check.status, doctor.WARN)
        self.assertIn("VPN ещё работает", check.hint)
        # Кнопку убирать нельзя: маршруты нужны активному VPN.
        self.assertNotIn("Убрать заглушки", check.hint)

