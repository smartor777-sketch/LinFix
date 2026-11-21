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
            "zapret.install_easy",
            ["--service", "zapret2", "--git-url", api.ZAPRET2_GIT_URL],
            timeout=1800,
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
        self.assertIn(["systemctl", "--user", "enable", "linfix-guard.timer"], calls)
        self.assertIn(["systemctl", "--user", "start", "linfix-guard.timer"], calls)

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


class CfDomainsTests(unittest.TestCase):
    def test_reads_state_domains(self) -> None:
        from linfix import state as state_mod

        state_info = SimpleNamespace(domains=("a.co.uk", "b.co.uk"), domains_updated_at="now")
        with patch("linfix.api.state.load_state", return_value=state_info), patch(
            "linfix.api.hostlist_path", return_value="/tmp/hl.txt"
        ), patch("linfix.hostlist.cf_domains", return_value=["a.co.uk"]):
            code, payload = api.get_cf_domains(Config())
        self.assertEqual(code, api.EXIT_OK)
        self.assertEqual(payload["domains"], ["a.co.uk", "b.co.uk"])
        self.assertEqual(payload["missing"], ["b.co.uk"])
        self.assertEqual(payload["in_hostlist"], 1)


class CfPathCheckTests(unittest.TestCase):
    def test_check_cf_path_counts_alive(self) -> None:
        fake = [("kws2.a.co.uk", True, "ok"), ("kws4.a.co.uk", False, "timeout")]
        with patch("linfix.api.cfdomains.test_domains", return_value=fake):
            result = api.check_cf_path(limit=1)
        self.assertEqual(result["ok"], 1)
        self.assertEqual(result["total"], 2)

    def test_check_cf_path_survives_exception(self) -> None:
        with patch("linfix.api.cfdomains.test_domains", side_effect=OSError("boom")):
            result = api.check_cf_path()
        self.assertEqual(result["ok"], 0)
        self.assertIn("boom", result["error"])

    def test_rolls_back_when_worse(self) -> None:
        cfg = Config()
        info = SimpleNamespace(service="zapret2", install_dir="/opt/zapret2")
        better = {"ok": 4, "total": 4}
        worse = {"ok": 0, "total": 4}
        with patch("linfix.api.zapret.detect_zapret", return_value=info), patch(
            "linfix.api.check_cf_path", side_effect=[better, worse]
        ), patch(
            "linfix.api.util.run_helper",
            side_effect=[(0, {"ok": True, "changed": True, "backup": "/tmp/bak"}),
                         (0, {"ok": True})],
        ) as helper, patch("linfix.api.util.run"):
            code, payload = api.neutralize_zapret_hostlist_args(cfg)

        self.assertEqual(code, api.EXIT_CHECK_FAILED)
        self.assertTrue(payload["rolled_back"])
        self.assertFalse(payload["ok"])
        # второй вызов — восстановление из бэкапа
        self.assertEqual(helper.call_args_list[1].args[0], "zapret.restore_config")

    def test_keeps_change_when_not_worse(self) -> None:
        cfg = Config()
        info = SimpleNamespace(service="zapret2", install_dir="/opt/zapret2")
        same = {"ok": 4, "total": 4}
        with patch("linfix.api.zapret.detect_zapret", return_value=info), patch(
            "linfix.api.check_cf_path", side_effect=[same, same]
        ), patch(
            "linfix.api.util.run_helper",
            return_value=(0, {"ok": True, "changed": True, "backup": "/tmp/bak"}),
        ), patch("linfix.api.util.run"):
            code, payload = api.neutralize_zapret_hostlist_args(cfg)
        self.assertEqual(code, api.EXIT_OK)
        self.assertTrue(payload["ok"])
        self.assertFalse(payload.get("rolled_back", False))
