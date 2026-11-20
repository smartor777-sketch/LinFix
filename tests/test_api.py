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


if __name__ == "__main__":
    unittest.main()
