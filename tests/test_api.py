"""Тесты linfix.api: start_tgws / start_zapret."""

from __future__ import annotations

import subprocess
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from linfix import api, util
from linfix.config import Config


def _cp(stdout: str = "", returncode: int = 0) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


class StartTgwsTests(unittest.TestCase):
    def test_starts(self) -> None:
        with patch("linfix.api.util.run", return_value=_cp()) as run:
            code, _ = api.start_tgws(Config())
        self.assertEqual(code, api.EXIT_OK)
        run.assert_called_once_with(
            ["systemctl", "--user", "start", "tgwsproxy"], check=False, timeout=30
        )

    def test_start_error(self) -> None:
        with patch("linfix.api.util.run", return_value=_cp("", 1)):
            code, msg = api.start_tgws(Config())
        self.assertEqual(code, api.EXIT_ERROR)
        self.assertIn("tgwsproxy", msg)


class StartZapretTests(unittest.TestCase):
    def test_restart_fails_when_unit_missing(self) -> None:
        info = SimpleNamespace(found=False, service="zapret")
        with patch("linfix.api.zapret.detect_zapret", return_value=info), patch(
            "linfix.api.util.run", return_value=_cp("inactive")
        ), patch(
            "linfix.api.util.run_helper", return_value=(5, {"error": "Unit zapret.service not found"})
        ):
            code, msg = api.start_zapret(Config())
        self.assertEqual(code, 5)
        self.assertIn("zapret", msg)

    def test_already_active(self) -> None:
        info = SimpleNamespace(found=True, service="zapret2")
        with patch("linfix.api.zapret.detect_zapret", return_value=info), patch(
            "linfix.api.util.run", return_value=_cp("active")
        ) as run:
            code, _ = api.start_zapret(Config())
        self.assertEqual(code, api.EXIT_OK)
        run.assert_called_once_with(["systemctl", "is-active", "zapret2"], check=False, timeout=10)

    def test_restart_ok(self) -> None:
        info = SimpleNamespace(found=True, service="zapret2")
        with patch("linfix.api.zapret.detect_zapret", return_value=info), patch(
            "linfix.api.util.run", return_value=_cp("inactive")
        ), patch(
            "linfix.api.util.run_helper", return_value=(0, {"ok": True})
        ) as run_helper:
            code, _ = api.start_zapret(Config())
        self.assertEqual(code, api.EXIT_OK)
        run_helper.assert_called_once_with("zapret.restart", ["--service", "zapret2"])

    def test_restart_error(self) -> None:
        info = SimpleNamespace(found=True, service="zapret2")
        with patch("linfix.api.zapret.detect_zapret", return_value=info), patch(
            "linfix.api.util.run", return_value=_cp("inactive")
        ), patch(
            "linfix.api.util.run_helper", return_value=(5, {"ok": False, "error": "no rights"})
        ):
            code, msg = api.start_zapret(Config())
        self.assertEqual(code, 5)
        self.assertEqual(msg, "no rights")

    def test_restart_error_no_rights(self) -> None:
        info = SimpleNamespace(found=True, service="zapret2")
        with patch("linfix.api.zapret.detect_zapret", return_value=info), patch(
            "linfix.api.util.run", return_value=_cp("inactive")
        ), patch(
            "linfix.api.util.run_helper", side_effect=util.NetfixError("no rights")
        ):
            code, msg = api.start_zapret(Config())
        self.assertEqual(code, api.EXIT_NO_RIGHTS)
        self.assertIn("no rights", msg)


class InstallZapret2Tests(unittest.TestCase):
    def test_already_installed(self) -> None:
        info = SimpleNamespace(found=True, service="zapret2", install_dir="/opt/zapret2")
        with patch("linfix.api.zapret.detect_zapret", return_value=info), patch(
            "linfix.api.util.run_helper"
        ) as run_helper:
            code, payload = api.install_zapret2(Config())
        self.assertEqual(code, api.EXIT_OK)
        run_helper.assert_not_called()
        self.assertTrue(payload["installed"])

    def test_install_via_helper(self) -> None:
        missing = SimpleNamespace(found=False, service="", install_dir="")
        found = SimpleNamespace(found=True, service="zapret2", install_dir="/opt/zapret2")
        with patch(
            "linfix.api.zapret.detect_zapret", side_effect=[missing, found]
        ), patch("linfix.api.util.run_helper", return_value=(0, {"ok": True})) as run_helper:
            code, payload = api.install_zapret2(Config())
        self.assertEqual(code, api.EXIT_OK)
        self.assertTrue(payload["installed"])
        self.assertEqual(payload["service"], "zapret2")
        run_helper.assert_called_once_with(
            "zapret.install_easy", ["--service", "zapret2", "--git-url", api.ZAPRET2_GIT_URL]
        )

    def test_install_without_rights(self) -> None:
        missing = SimpleNamespace(found=False, service="", install_dir="")
        with patch("linfix.api.zapret.detect_zapret", return_value=missing), patch(
            "linfix.api.util.run_helper", side_effect=util.NetfixError("no rights")
        ):
            code, payload = api.install_zapret2(Config())
        self.assertEqual(code, api.EXIT_NO_RIGHTS)
        self.assertFalse(payload["ok"])
        self.assertIn("no rights", payload["error"])


class EnableAutostartTests(unittest.TestCase):
    def test_enables_tgws_as_user_and_zapret_via_helper(self) -> None:
        info = SimpleNamespace(service="zapret2")
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            calls.append(list(cmd))
            return _cp("", 0)

        with patch("linfix.api.zapret.detect_zapret", return_value=info), patch(
            "linfix.api.util.run", side_effect=fake_run
        ), patch(
            "linfix.api.util.run_helper", return_value=(0, {"ok": True, "enabled": True})
        ) as run_helper, patch(
            "linfix.api.start_zapret", return_value=(api.EXIT_OK, "ok")
        ) as start_zapret:
            code, payload = api.enable_autostart(Config())

        self.assertEqual(code, api.EXIT_OK)
        self.assertTrue(payload["ok"])
        self.assertIn(["systemctl", "--user", "enable", "tgwsproxy"], calls)
        self.assertIn(["systemctl", "--user", "start", "tgwsproxy"], calls)
        run_helper.assert_called_once_with(
            "zapret.set_enabled", ["--service", "zapret2", "--enabled", "on"]
        )
        start_zapret.assert_called_once()

    def test_zapret_error_is_reported(self) -> None:
        info = SimpleNamespace(service="zapret2")

        with patch("linfix.api.zapret.detect_zapret", return_value=info), patch(
            "linfix.api.util.run", return_value=_cp("", 0)
        ), patch(
            "linfix.api.util.run_helper", return_value=(5, {"ok": False, "enabled": False})
        ), patch(
            "linfix.api.start_zapret", return_value=(api.EXIT_NO_RIGHTS, "no rights")
        ):
            code, payload = api.enable_autostart(Config())

        self.assertEqual(code, api.EXIT_OK)
        self.assertFalse(payload["ok"])
        self.assertFalse(payload["enabled"]["zapret2"])
        self.assertFalse(payload["started"]["zapret2"])


if __name__ == "__main__":
    unittest.main()
