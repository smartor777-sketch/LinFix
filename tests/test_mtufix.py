"""Тесты исправления PMTU: чтение состояния, вызовы helper, границы."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest import mock

from linfix import mtufix


def _cp(stdout: str = "", code: int = 0):
    import subprocess

    return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr="")


class StateTests(unittest.TestCase):
    def test_reads_interface_and_mtu(self) -> None:
        def fake_run(cmd, **_kwargs):
            if "route" in cmd:
                return _cp("104.21.32.39 via 192.168.3.1 dev wlo1 src 192.168.3.11\n")
            if "link" in cmd:
                return _cp("2: wlo1: <BROADCAST> mtu 1500 qdisc noqueue state UP\n")
            return _cp()

        with mock.patch.object(mtufix.util, "run", side_effect=fake_run):
            state = mtufix.read_state("104.21.32.39")
        self.assertEqual(state.interface, "wlo1")
        self.assertEqual(state.interface_mtu, 1500)

    def test_describe(self) -> None:
        state = mtufix.MtuState(
            interface="wlo1", interface_mtu=1500, probing=0, link_mtu=1492, link_mtu_source="1492"
        )
        text = mtufix.describe(state)
        self.assertIn("wlo1", text)
        self.assertIn("tcp_mtu_probing=0", text)


class ProbingTests(unittest.TestCase):
    def test_enable_probing_success(self) -> None:
        with mock.patch.object(mtufix.util, "run_helper", return_value=(0, {"ok": True})) as helper, \
             mock.patch.object(mtufix, "read_state", return_value=mtufix.MtuState("wlo1", 1500, 1, None, None)):
            code, payload = mtufix.enable_probing()
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        helper.assert_called_once_with(
            "sysctl.set", ["--key", "net.ipv4.tcp_mtu_probing", "--value", "1"], timeout=60
        )

    def test_enable_probing_without_rights(self) -> None:
        with mock.patch.object(
            mtufix.util, "run_helper", side_effect=mtufix.util.NetfixError("нет прав")
        ):
            code, payload = mtufix.enable_probing()
        self.assertEqual(code, 4)
        self.assertIn("нет прав", payload["error"])


class InterfaceMtuTests(unittest.TestCase):
    def test_rejects_bad_interface(self) -> None:
        for name in ("", "wlo1; rm -rf /", "a b", "x" * 40):
            with self.subTest(name=name):
                code, payload = mtufix.set_interface_mtu(name, 1492)
                self.assertEqual(code, 2)

    def test_rejects_out_of_range(self) -> None:
        for mtu in (0, 100, 1501, 9000):
            with self.subTest(mtu=mtu):
                code, _ = mtufix.set_interface_mtu("wlo1", mtu)
                self.assertEqual(code, 2)

    def test_sets_mtu_via_helper(self) -> None:
        with mock.patch.object(mtufix.util, "run_helper", return_value=(0, {"ok": True})) as helper, \
             mock.patch.object(mtufix, "read_state", return_value=mtufix.MtuState("wlo1", 1492, 0, None, None)):
            code, payload = mtufix.set_interface_mtu("wlo1", 1492)
        self.assertEqual(code, 0)
        self.assertEqual(payload["mtu"], 1492)
        helper.assert_called_once_with(
            "mtu.set", ["--interface", "wlo1", "--mtu", "1492"], timeout=60
        )


if __name__ == "__main__":
    unittest.main()
