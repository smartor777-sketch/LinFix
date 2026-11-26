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
        with mock.patch.object(doctor, "ipv6_blackhole_routes", return_value=[]):
            with mock.patch.object(doctor.guard, "load_event", return_value={}):
                check = doctor._guard_check(config.Config())
        self.assertEqual(check.status, doctor.PASS)

    def test_last_trip(self) -> None:
        event = {"last_trip": "2026-11-20T10:00:00+03:00", "detail": "CF-путь недоступен"}
        with mock.patch.object(doctor, "ipv6_blackhole_routes", return_value=[]):
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
        # Маршруты принадлежат активному VPN: это не висячие заглушки.
        self.assertEqual(check.status, doctor.PASS)
        self.assertIn("VPN активен", check.details)
        self.assertNotIn("Убрать", check.hint)

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
        # Маршруты нужны активному VPN — удалять их нельзя.
        self.assertEqual(check.status, doctor.PASS)
        self.assertIn("VPN активен", check.details)
        self.assertNotIn("Убрать заглушки", check.hint)



class GuardStaleEventTests(unittest.TestCase):
    """Событие guard — запись о прошлой находке, а не текущее состояние.

    Баг: заглушки убрали, а «Диагностика» продолжала показывать WARN,
    потому что читала только сохранённое событие.
    """

    def _run(self, event: dict, live_routes: list[str], vpn: list[str] | None = None):
        from dataclasses import replace

        cfg = replace(config.Config(), guard=replace(config.Config().guard, enabled=True))
        with mock.patch.object(doctor.guard, "load_event", return_value=event):
            with mock.patch.object(
                doctor, "ipv6_blackhole_routes", return_value=live_routes
            ):
                with mock.patch.object(
                    doctor, "vpn_interfaces", return_value=vpn or []
                ):
                    return doctor._guard_check(cfg)

    def test_stale_event_shows_pass(self) -> None:
        event = {"ipv6_blackholes": 2, "last_trip": "2026-11-23T21:03:10+03:00"}
        check = self._run(event, [])
        self.assertEqual(check.status, doctor.PASS)
        self.assertIn("убраны", check.details)
        self.assertIn("2", check.details)
        self.assertEqual(check.hint, "")

    def test_live_routes_still_warn(self) -> None:
        event = {"ipv6_blackholes": 2}
        check = self._run(event, ["blackhole 2000::/4", "blackhole ::/1"])
        self.assertEqual(check.status, doctor.WARN)
        self.assertIn("2", check.details)

    def test_live_routes_without_event_warn(self) -> None:
        """Заглушки есть, события нет — всё равно предупреждаем."""

        check = self._run({}, ["blackhole 2000::/4"])
        self.assertEqual(check.status, doctor.WARN)
        self.assertIn("1", check.details)

    def test_live_routes_with_active_vpn_pass(self) -> None:
        """Маршруты активного VPN — не висячие заглушки, не пугаем."""

        check = self._run(
            {"ipv6_blackholes": 5},
            ["blackhole 2000::/4", "blackhole ::/1"],
            vpn=["qeli"],
        )
        self.assertEqual(check.status, doctor.PASS)
        self.assertIn("VPN активен", check.details)
        self.assertNotIn("висячих", check.details)

    def test_clean_state_and_no_event(self) -> None:
        check = self._run({}, [])
        # Дальше обычная логика guard; главное — не про заглушки.
        self.assertNotIn("заглуш", check.details)
