from __future__ import annotations

import json
import stat
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from linfix import tgws

DIGEST = "sha256:" + "a" * 64
RAW_RELEASE = {
    "tag_name": "v1.11.4",
    "assets": [
        {
            "name": "TgWsProxy_linux_amd64.deb",
            "browser_download_url": "https://example.com/x.deb",
            "digest": "sha256:" + "b" * 64,
            "size": 10,
        },
        {
            "name": "TgWsProxy_linux_amd64",
            "browser_download_url": "https://example.com/x",
            "digest": DIGEST,
            "size": 57377032,
        },
    ],
}


class ReleaseTests(unittest.TestCase):
    def test_parse_and_select(self) -> None:
        release = tgws.parse_release(RAW_RELEASE)
        self.assertEqual(release.tag, "v1.11.4")
        asset = tgws.select_linux_asset(release)
        self.assertEqual(asset.name, "TgWsProxy_linux_amd64")
        self.assertEqual(asset.sha256, "a" * 64)

    def test_never_selects_deb(self) -> None:
        release = tgws.parse_release(RAW_RELEASE)
        self.assertNotEqual(tgws.select_linux_asset(release).name, "TgWsProxy_linux_amd64.deb")

    def test_missing_digest(self) -> None:
        raw = {
            "tag_name": "v1",
            "assets": [
                {
                    "name": "TgWsProxy_linux_amd64",
                    "browser_download_url": "https://example.com/x",
                    "digest": "",
                    "size": 1,
                }
            ],
        }
        with self.assertRaises(tgws.TgwsError):
            tgws.select_linux_asset(tgws.parse_release(raw))

    def test_missing_asset(self) -> None:
        with self.assertRaises(tgws.TgwsError):
            tgws.select_linux_asset(tgws.parse_release({"tag_name": "v1", "assets": []}))

    def test_normalize_version(self) -> None:
        self.assertEqual(tgws.normalize_version("1.11.4"), "v1.11.4")
        self.assertEqual(tgws.normalize_version("v1.11.4"), "v1.11.4")
        self.assertIsNone(tgws.normalize_version(None))
        with self.assertRaises(tgws.TgwsError):
            tgws.normalize_version("v1; rm -rf /")


class InstallBinaryTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_installs_with_correct_sha_and_mode(self) -> None:
        source = self.root / "download"
        source.write_bytes(b"binary-content")
        expected = tgws.sha256_file(source)
        dest = self.root / "TgWsProxy"

        info = tgws.install_binary(source, dest, expected)

        self.assertEqual(dest.read_bytes(), b"binary-content")
        self.assertEqual(info["sha256"], expected)
        self.assertEqual(stat.S_IMODE(dest.stat().st_mode), 0o755)

    def test_wrong_sha_does_not_touch_destination(self) -> None:
        source = self.root / "download"
        source.write_bytes(b"binary-content")
        dest = self.root / "TgWsProxy"
        dest.write_bytes(b"old")

        with self.assertRaises(tgws.TgwsError):
            tgws.install_binary(source, dest, "0" * 64)

        self.assertEqual(dest.read_bytes(), b"old")

    def test_http_download_rejected(self) -> None:
        with self.assertRaises(tgws.TgwsError):
            tgws.download_to_temp("http://example.com/x", self.root)


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_default_config(self) -> None:
        data = tgws.default_config(1081, "127.0.0.1")
        self.assertEqual(data["port"], 1081)
        self.assertEqual(data["host"], "127.0.0.1")
        self.assertTrue(data["cfproxy"])
        self.assertTrue(data["h2"])
        self.assertFalse(data["verbose"])
        self.assertEqual(data["pool_size"], 4)
        self.assertEqual(data["log_max_mb"], 5)
        self.assertRegex(str(data["secret"]), r"^[0-9a-f]{32}$")

    def test_create_and_find(self) -> None:
        path, created = tgws.ensure_config(create=True, port=1082, home=self.root)
        self.assertTrue(created)
        self.assertIsNotNone(path)
        assert path is not None
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertEqual(tgws.find_config_path(self.root), path)

        again, created_again = tgws.ensure_config(create=True, port=9999, home=self.root)
        self.assertEqual(again, path)
        self.assertFalse(created_again)
        self.assertEqual(tgws.load_json_config(path)["port"], 1082)

    def test_update_preserves_unknown_and_secret(self) -> None:
        path, _ = tgws.ensure_config(create=True, home=self.root)
        assert path is not None
        data = tgws.load_json_config(path)
        secret = data["secret"]
        data["custom_key"] = "keep-me"
        path.write_text(json.dumps(data), encoding="utf-8")

        updated, backup, changed = tgws.update_config(path, {"port": 1083, "verbose": True})

        self.assertTrue(changed)
        self.assertIsNotNone(backup)
        self.assertTrue(backup.exists())
        self.assertEqual(updated["port"], 1083)
        self.assertTrue(updated["verbose"])
        self.assertEqual(updated["custom_key"], "keep-me")
        self.assertEqual(updated["secret"], secret)
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_no_change_no_backup(self) -> None:
        path, _ = tgws.ensure_config(create=True, home=self.root)
        assert path is not None
        _, backup, changed = tgws.update_config(path, {"port": 1080})
        self.assertFalse(changed)
        self.assertIsNone(backup)
        self.assertEqual(list(self.root.glob("**/*.bak-linfix-*")), [])

    def test_invalid_values(self) -> None:
        path, _ = tgws.ensure_config(create=True, home=self.root)
        assert path is not None
        for changes in (
            {"port": "1080"},
            {"port": 70000},
            {"pool_size": 0},
            {"h2": "yes"},
            {"host": "0.0.0.0"},
            {"secret": "x"},
        ):
            with self.subTest(changes=changes):
                with self.assertRaises(tgws.TgwsError):
                    tgws.update_config(path, changes)

    def test_loopback_hosts_allowed(self) -> None:
        path, _ = tgws.ensure_config(create=True, home=self.root)
        assert path is not None
        for host in ("127.0.0.1", "localhost", "::1"):
            with self.subTest(host=host):
                updated, _, _ = tgws.update_config(path, {"host": host})
                self.assertEqual(updated["host"], host)

    def test_log_max_mb_default_and_validation(self) -> None:
        data = tgws.default_config()
        self.assertEqual(data["log_max_mb"], 5)

        path, _ = tgws.ensure_config(create=True, home=self.root)
        assert path is not None
        updated, _, changed = tgws.update_config(path, {"log_max_mb": 12.5})
        self.assertTrue(changed)
        self.assertEqual(updated["log_max_mb"], 12.5)

        for bad in (0, 0.1, 101, "5", True):
            with self.subTest(bad=bad):
                with self.assertRaises(tgws.TgwsError):
                    tgws.update_config(path, {"log_max_mb": bad})

    def test_mode_saved_outside_tgws_config(self) -> None:
        # TgWsProxy при старте затирает незнакомые ключи в своём config.json,
        # поэтому режим хранится в отдельном файле LinFix.
        mode_path = tgws.save_mode(tgws.MODE_CLI)
        self.addCleanup(mode_path.unlink, missing_ok=True)
        self.assertEqual(mode_path.read_text(encoding="utf-8").strip(), tgws.MODE_CLI)
        self.assertEqual(tgws.detect_mode(None), tgws.MODE_CLI)

        tgws.save_mode(tgws.MODE_TRAY)
        with mock.patch.object(tgws, "display_available", return_value=True):
            self.assertEqual(tgws.detect_mode(None), tgws.MODE_TRAY)

        with self.assertRaises(tgws.TgwsError):
            tgws.save_mode("gui")

    def test_detect_mode_reads_legacy_key(self) -> None:
        mode_path = tgws.mode_file()
        if mode_path.exists():
            self.addCleanup(mode_path.write_text, mode_path.read_text(encoding="utf-8"))
            mode_path.unlink()
        path, _ = tgws.ensure_config(create=True, home=self.root)
        assert path is not None
        tgws.update_config(path, {"linfix_mode": tgws.MODE_CLI})
        self.assertFalse(mode_path.exists())
        self.assertEqual(tgws.detect_mode(path), tgws.MODE_CLI)

    def test_effective_mode_falls_back_to_cli_without_x11(self) -> None:
        mode_path = tgws.save_mode(tgws.MODE_TRAY)
        self.addCleanup(mode_path.unlink, missing_ok=True)
        with mock.patch.object(tgws, "display_available", return_value=False):
            self.assertEqual(tgws.effective_mode(None), tgws.MODE_CLI)
        with mock.patch.object(tgws, "display_available", return_value=True):
            self.assertEqual(tgws.effective_mode(None), tgws.MODE_TRAY)

    def test_cli_unit_has_no_display(self) -> None:
        data = tgws.default_config(1081, "127.0.0.1")
        data["linfix_mode"] = tgws.MODE_CLI
        unit = tgws.render_unit(
            "/opt/tgwsproxy/TgWsProxy",
            mode=tgws.MODE_CLI,
            data=data,
            source="/opt/tgwsproxy/src",
            python="/usr/bin/python3",
        )
        self.assertNotIn("DISPLAY", unit)
        self.assertIn("PYTHONPATH=/opt/tgwsproxy/src", unit)
        self.assertIn("-m proxy.tg_ws_proxy", unit)
        self.assertIn("--port 1081", unit)
        self.assertIn("--secret", unit)
        self.assertIn("--log-file", unit)

    def test_cli_args_from_config(self) -> None:
        data = tgws.default_config(1090, "127.0.0.1")
        data["verbose"] = True
        data["cfproxy"] = False
        data["h2"] = False
        args = tgws.cli_args(data)
        self.assertIn("--port", args)
        self.assertEqual(args[args.index("--port") + 1], "1090")
        self.assertIn("--verbose", args)
        self.assertIn("--no-cfproxy", args)
        self.assertIn("--no-h2", args)

    def test_tray_unit_keeps_display(self) -> None:
        unit = tgws.render_unit("/opt/tgwsproxy/TgWsProxy", display=":7")
        self.assertIn("Environment=DISPLAY=:7", unit)
        self.assertIn("StartLimitBurst=5", unit)

    def test_read_link(self) -> None:
        path, _ = tgws.ensure_config(create=True, home=self.root)
        assert path is not None
        data = tgws.load_json_config(path)
        link = tgws.read_link(home=self.root)
        self.assertEqual(
            link,
            f"tg://proxy?server=127.0.0.1&port=1080&secret=dd{data['secret']}",
        )


class UnitTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_install_user_unit_idempotent(self) -> None:
        path, changed = tgws.install_user_unit(
            "/opt/tgwsproxy/TgWsProxy", home=self.root, systemctl=False
        )
        self.assertTrue(changed)
        text = path.read_text(encoding="utf-8")
        self.assertIn("ExecStart=/opt/tgwsproxy/TgWsProxy", text)
        self.assertIn("WantedBy=default.target", text)
        self.assertIn("Environment=DISPLAY=", text)

        again, changed_again = tgws.install_user_unit(
            "/opt/tgwsproxy/TgWsProxy", home=self.root, systemctl=False
        )
        self.assertEqual(again, path)
        self.assertFalse(changed_again)


if __name__ == "__main__":
    unittest.main()


class RunningModeTests(unittest.TestCase):
    def _cp(self, stdout: str, code: int = 0):
        import subprocess

        return subprocess.CompletedProcess(args=[], returncode=code, stdout=stdout, stderr="")

    def test_cli_process_detected(self) -> None:
        out = (
            "123 /usr/bin/python3 -m proxy.tg_ws_proxy --port 1080 "
            "--secret abc\n"
        )
        with mock.patch.object(tgws.util, "run", return_value=self._cp(out)):
            self.assertEqual(tgws.running_mode(), tgws.MODE_CLI)

    def test_tray_process_detected(self) -> None:
        out = "456 /opt/tgwsproxy/TgWsProxy\n"
        with mock.patch.object(tgws.util, "run", return_value=self._cp(out)):
            self.assertEqual(tgws.running_mode(), tgws.MODE_TRAY)

    def test_no_process(self) -> None:
        with mock.patch.object(tgws.util, "run", return_value=self._cp("", 1)):
            self.assertIsNone(tgws.running_mode())
