from __future__ import annotations

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
        load.assert_called_once()
        save.assert_called_once()

    def test_http_url_rejected(self) -> None:
        with self.assertRaises(helper.HelperError):
            helper.action_tgws_install(self.args(url="http://example.com/x"))

    def test_bad_sha_rejected(self) -> None:
        with self.assertRaises(helper.HelperError):
            helper.action_tgws_install(self.args(sha256="abc"))


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
