from __future__ import annotations

import signal
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from linfix import helper


class PathValidationTests(unittest.TestCase):
    def test_allows_paths_under_zapret(self) -> None:
        path = helper.validate_path(
            "/opt/zapret2/ipset/zapret-hosts-user.txt", must_exist=False
        )
        self.assertEqual(str(path), "/opt/zapret2/ipset/zapret-hosts-user.txt")

    def test_rejects_outside_allowed_roots(self) -> None:
        with self.assertRaises(helper.HelperError):
            helper.validate_path("/etc/passwd")

    def test_rejects_relative_path(self) -> None:
        with self.assertRaises(helper.HelperError):
            helper.validate_path("opt/zapret2/ipset/hosts")

    def test_rejects_traversal(self) -> None:
        with self.assertRaises(helper.HelperError):
            helper.validate_path("/opt/zapret2/../../etc/passwd")

    def test_rejects_symlink_pointing_outside(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "passwd"
            link.symlink_to("/etc/passwd")
            with self.assertRaises(helper.HelperError):
                helper.validate_path(str(link), must_exist=True)

    def test_rejects_gzip_list(self) -> None:
        with self.assertRaises(helper.HelperError):
            helper.validate_path("/opt/zapret2/ipset/zapret-hosts.txt.gz", must_exist=False)


class DomainValidationTests(unittest.TestCase):
    def test_valid_and_normalized(self) -> None:
        self.assertEqual(helper.validate_domain("  PCLEAD.CO.UK "), "pclead.co.uk")

    def test_invalid(self) -> None:
        for value in ("bad_domain", "http://x.com", "-bad.com", ""):
            with self.subTest(value=value):
                with self.assertRaises(helper.HelperError):
                    helper.validate_domain(value)


class ActionValidationTests(unittest.TestCase):
    def test_known_actions(self) -> None:
        self.assertEqual(helper.validate_action("domains.merge"), "domains.merge")
        self.assertEqual(helper.validate_action("zapret.restart"), "zapret.restart")
        self.assertEqual(helper.validate_action("zapret.install_easy"), "zapret.install_easy")

    def test_unknown_action(self) -> None:
        with self.assertRaises(helper.HelperError):
            helper.validate_action("shell.exec")


class EnabledValidationTests(unittest.TestCase):
    def test_valid(self) -> None:
        self.assertEqual(helper.validate_enabled("on"), "on")
        self.assertEqual(helper.validate_enabled("off"), "off")

    def test_invalid(self) -> None:
        with self.assertRaises(helper.HelperError):
            helper.validate_enabled("yes")
        with self.assertRaises(helper.HelperError):
            helper.validate_enabled("")


class ServiceValidationTests(unittest.TestCase):
    def test_known_services(self) -> None:
        self.assertEqual(helper.validate_service("zapret"), "zapret")
        self.assertEqual(helper.validate_service("zapret2"), "zapret2")

    def test_unknown_service(self) -> None:
        with self.assertRaises(helper.HelperError):
            helper.validate_service("ssh")


class ConfigGuardActionTests(unittest.TestCase):
    def test_updates_existing_config_with_backup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            path.write_text(
                "[general]\nlog_level = \"info\"\n\n[guard]\nenabled = false\n",
                encoding="utf-8",
            )
            with mock.patch.object(
                helper, "validate_path", side_effect=lambda raw, must_exist=False: Path(raw)
            ):
                code, payload = helper.action_config_guard(
                    SimpleNamespace(config=str(path), enabled="on")
                )

            self.assertEqual(code, 0)
            self.assertTrue(payload["changed"])
            self.assertTrue(Path(str(payload["backup"])).exists())
            text = path.read_text(encoding="utf-8")
            self.assertIn("enabled = true", text)
            self.assertIn("log_level = \"info\"", text)

    def test_creates_missing_config(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config.toml"
            with mock.patch.object(
                helper, "validate_path", side_effect=lambda raw, must_exist=False: Path(raw)
            ):
                code, payload = helper.action_config_guard(
                    SimpleNamespace(config=str(path), enabled="off")
                )
            self.assertEqual(code, 0)
            self.assertIsNone(payload["backup"])
            self.assertIn("enabled = false", path.read_text(encoding="utf-8"))


class ProfileActionsTests(unittest.TestCase):
    UUID = "11111111-2222-3333-4444-555555555555"

    def args(self, **overrides):
        values = {"uuid": self.UUID, "name": "Дом", "strategy": "general"}
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_profile_set(self) -> None:
        with mock.patch.object(
            helper.profiles,
            "set_profile",
            return_value={
                "profiles": {self.UUID: helper.profiles.Profile(name="Дом", strategy="general")},
                "changed": True,
                "uuid": self.UUID,
            },
        ):
            code, payload = helper.action_profile_set(self.args())
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["name"], "Дом")
        self.assertIn(self.UUID, payload["profiles"])

    def test_profile_set_validates_uuid(self) -> None:
        with self.assertRaises(helper.HelperError):
            helper.action_profile_set(self.args(uuid="bad"))

    def test_profile_clear(self) -> None:
        with mock.patch.object(
            helper.profiles,
            "clear_profile",
            return_value={"profiles": {}, "changed": True, "uuid": self.UUID},
        ):
            code, payload = helper.action_profile_clear(self.args(name=None))
        self.assertEqual(code, 0)
        self.assertTrue(payload["changed"])

    def test_profile_apply(self) -> None:
        with mock.patch.object(
            helper.profiles,
            "load_profiles",
            return_value={self.UUID: helper.profiles.Profile(name="Дом", strategy="general")},
        ):
            code, payload = helper.action_profile_apply(self.args(name=None))
        self.assertEqual(code, 0)
        self.assertEqual(payload["name"], "Дом")
        self.assertEqual(payload["strategy"], "general")

    def test_profile_apply_missing(self) -> None:
        with mock.patch.object(helper.profiles, "load_profiles", return_value={}):
            code, payload = helper.action_profile_apply(self.args(name=None))
        self.assertEqual(code, 0)
        self.assertEqual(payload["name"], "general")


class TgwsInstallActionTests(unittest.TestCase):
    def args(self, **overrides):
        values = {
            "url": "https://example.com/TgWsProxy_linux_amd64",
            "sha256": "a" * 64,
            "dest": "/opt/tgwsproxy/TgWsProxy",
            "state": "/var/lib/linfix/state.json",
            "version": "v1.11.4",
            "timeout": 1.0,
        }
        values.update(overrides)
        return SimpleNamespace(**values)

    def test_installs_and_updates_state(self) -> None:
        with (
            mock.patch.object(
                helper.tgws,
                "install_from_url",
                return_value={"sha256": "a" * 64, "size": 123},
            ) as install,
            mock.patch.object(helper.state, "load_state", return_value=helper.state.State()) as load,
            mock.patch.object(helper.state.State, "save", return_value=Path("/var/lib/linfix/state.json")) as save,
        ):
            code, payload = helper.action_tgws_install(self.args())

        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["version"], "v1.11.4")
        self.assertEqual(payload["size"], 123)
        install.assert_called_once()
        # Фоновые потоки GTK-тестов тоже читают state — проверяем свой вызов.
        load.assert_any_call(Path("/var/lib/linfix/state.json"))
        save.assert_called_once()

    def test_http_url_rejected(self) -> None:
        with self.assertRaises(helper.HelperError):
            helper.action_tgws_install(self.args(url="http://example.com/x"))

    def test_bad_sha_rejected(self) -> None:
        with self.assertRaises(helper.HelperError):
            helper.action_tgws_install(self.args(sha256="abc"))


class ZapretBlockcheckStopTests(unittest.TestCase):
    def _args(self) -> SimpleNamespace:
        # install_dir должен существовать и содержать blockcheck2.sh.
        tmp = Path(tempfile.mkdtemp(prefix="linfix-bc-stop-"))
        (tmp / "blockcheck2.sh").write_text("#!/bin/sh\n", encoding="utf-8")
        self.addCleanup(lambda: __import__("shutil").rmtree(tmp, ignore_errors=True))
        return SimpleNamespace(service="zapret2", install_dir=tmp)

    def test_stop_and_start_when_inactive(self) -> None:
        """Прибить процессы замеров и поднять zapret, если он выключен."""

        from linfix import blockcheck

        active = mock.Mock(returncode=0, stdout="inactive", stderr="")
        with mock.patch.object(
            blockcheck, "stray_processes", side_effect=[[11, 12], [], []]
        ), mock.patch.object(helper.os, "kill") as kill, mock.patch.object(
            helper.util, "run", return_value=active
        ) as run:
            code, payload = helper.action_zapret_blockcheck_stop(self._args())
        self.assertEqual(code, 0)
        self.assertEqual(payload["stopped"], [11, 12])
        kill.assert_any_call(11, signal.SIGTERM)
        kill.assert_any_call(12, signal.SIGTERM)
        starts = [c for c in run.call_args_list if "start" in c.args[0]]
        self.assertTrue(starts, f"expected systemctl start, got {run.call_args_list}")

    def test_no_stray_no_start_when_active(self) -> None:
        """Нет замеров и запущен zapret — ничего не делать."""

        from linfix import blockcheck

        active = mock.Mock(returncode=0, stdout="active", stderr="")
        with mock.patch.object(
            blockcheck, "stray_processes", side_effect=[[], []]
        ), mock.patch.object(helper.os, "kill") as kill, mock.patch.object(
            helper.util, "run", return_value=active
        ) as run:
            code, payload = helper.action_zapret_blockcheck_stop(self._args())
        self.assertEqual(code, 0)
        self.assertEqual(payload["stopped"], [])
        kill.assert_not_called()
        starts = [c for c in run.call_args_list if "start" in c.args[0]]
        self.assertFalse(starts)

    def test_no_stray_but_inactive_starts(self) -> None:
        from linfix import blockcheck

        inactive = mock.Mock(returncode=0, stdout="inactive", stderr="")
        with mock.patch.object(
            blockcheck, "stray_processes", side_effect=[[], []]
        ), mock.patch.object(helper.os, "kill"), mock.patch.object(
            helper.util, "run", return_value=inactive
        ) as run:
            code, _ = helper.action_zapret_blockcheck_stop(self._args())
        self.assertEqual(code, 0)
        starts = [c for c in run.call_args_list if "start" in c.args[0]]
        self.assertTrue(starts)


class ZapretInstallEasyTests(unittest.TestCase):
    def test_http_url_rejected(self) -> None:
        args = SimpleNamespace(service="zapret2", git_url="http://example.com/zapret", timeout=5.0)
        with self.assertRaises(helper.HelperError):
            helper.action_zapret_install_easy(args)

    def test_invalid_service_rejected(self) -> None:
        args = SimpleNamespace(service="evil", git_url="https://example.com/z", timeout=5.0)
        with self.assertRaises(helper.HelperError):
            helper.action_zapret_install_easy(args)

    def test_existing_install_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp)
            (target / "install_easy.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            args = SimpleNamespace(
                service="zapret2", git_url="https://example.com/z", timeout=5.0
            )
            fake = mock.Mock(returncode=0, stdout="", stderr="")
            with mock.patch.object(helper, "Path", return_value=target), mock.patch.object(
                helper.util, "run", return_value=fake
            ) as run:
                code, payload = helper.action_zapret_install_easy(args)
        self.assertEqual(code, 0)
        self.assertTrue(payload["already_installed"])
        self.assertFalse(payload["cloned"])
        run.assert_called_once_with(
            ["systemctl", "cat", "zapret2"], check=False, timeout=10
        )


if __name__ == "__main__":
    unittest.main()


class SysctlAndMtuTests(unittest.TestCase):
    def test_sysctl_rejects_unknown_key(self) -> None:
        args = SimpleNamespace(key="net.ipv4.ip_forward", value="1", json=False)
        with self.assertRaises(helper.HelperError):
            helper.action_sysctl_set(args)

    def test_sysctl_rejects_bad_value(self) -> None:
        args = SimpleNamespace(key="net.ipv4.tcp_mtu_probing", value="9", json=False)
        with self.assertRaises(helper.HelperError):
            helper.action_sysctl_set(args)

    def test_mtu_never_increases(self) -> None:
        running = mock.Mock(returncode=0, stdout="2: wlo1: mtu 1400 qdisc\n", stderr="")
        args = SimpleNamespace(interface="wlo1", mtu=1492, json=False)
        with mock.patch.object(helper.util, "run", return_value=running):
            with self.assertRaises(helper.HelperError):
                helper.action_mtu_set(args)

    def test_mtu_rejects_missing_interface(self) -> None:
        missing = mock.Mock(returncode=1, stdout="", stderr="not found")
        args = SimpleNamespace(interface="nope0", mtu=1492, json=False)
        with mock.patch.object(helper.util, "run", return_value=missing):
            with self.assertRaises(helper.HelperError):
                helper.action_mtu_set(args)


class MtuIncreaseFlagTests(unittest.TestCase):
    def test_increase_requires_flag(self) -> None:
        running = mock.Mock(returncode=0, stdout="2: wlo1: mtu 1400 qdisc\n", stderr="")
        args = SimpleNamespace(interface="wlo1", mtu=1492, json=False, allow_increase=False)
        with mock.patch.object(helper.util, "run", return_value=running):
            with self.assertRaises(helper.HelperError):
                helper.action_mtu_set(args)

    def test_increase_allowed_with_flag(self) -> None:
        calls: list[list[str]] = []

        def fake_run(cmd, **_kwargs):
            calls.append(list(cmd))
            if "link" in cmd and "show" in cmd:
                return mock.Mock(returncode=0, stdout="2: wlo1: mtu 1492 qdisc\n", stderr="")
            return mock.Mock(returncode=0, stdout="", stderr="")

        args = SimpleNamespace(interface="wlo1", mtu=1492, json=False, allow_increase=True)
        with mock.patch.object(helper.util, "run", side_effect=fake_run):
            code, payload = helper.action_mtu_set(args)
        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertIn(["ip", "link", "set", "wlo1", "mtu", "1492"], calls)
