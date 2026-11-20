from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from netfix.config import ConfigError, DEFAULT_MARKER, load_config


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def write(self, text: str) -> Path:
        path = self.root / "config.toml"
        path.write_text(text, encoding="utf-8")
        return path

    def test_missing_file_uses_defaults(self) -> None:
        cfg = load_config(self.root / "nope.toml")
        self.assertIsNone(cfg.source)
        self.assertEqual(cfg.general.log_level, "info")
        self.assertEqual(cfg.general.language, "ru")
        self.assertEqual(cfg.zapret.install_dir, "/opt/zapret")
        self.assertEqual(cfg.zapret.service, "zapret")
        self.assertTrue(cfg.zapret.restart_on_change)
        self.assertEqual(cfg.tgws.port, 1080)
        self.assertEqual(cfg.tgws.host, "127.0.0.1")
        self.assertEqual(cfg.lists.update_days, 7)
        self.assertEqual(cfg.lists.marker, DEFAULT_MARKER)
        self.assertTrue(cfg.guard.enabled)
        self.assertEqual(cfg.guard.error_threshold, 20)
        self.assertFalse(cfg.profiles.enabled)
        self.assertTrue(cfg.warnings)

    def test_partial_override(self) -> None:
        path = self.write(
            """
[zapret]
install_dir = "/opt/zapret2"
service = "zapret2"
hostlist = "/opt/zapret2/ipset/zapret-hosts-user.txt"

[tgws]
port = 1081

[guard]
enabled = false
"""
        )
        cfg = load_config(path)
        self.assertEqual(cfg.zapret.install_dir, "/opt/zapret2")
        self.assertEqual(cfg.zapret.service, "zapret2")
        self.assertEqual(cfg.zapret.hostlist, "/opt/zapret2/ipset/zapret-hosts-user.txt")
        self.assertEqual(cfg.tgws.port, 1081)
        self.assertFalse(cfg.guard.enabled)
        self.assertEqual(cfg.guard.window_seconds, 60)

    def test_wrong_type_raises(self) -> None:
        path = self.write('[tgws]\nport = "1080"\n')
        with self.assertRaises(ConfigError):
            load_config(path)

    def test_bool_is_not_int(self) -> None:
        path = self.write("[guard]\nerror_threshold = true\n")
        with self.assertRaises(ConfigError):
            load_config(path)

    def test_invalid_port_range(self) -> None:
        path = self.write("[tgws]\nport = 70000\n")
        with self.assertRaises(ConfigError):
            load_config(path)

    def test_invalid_toml(self) -> None:
        path = self.write("[zapret\n")
        with self.assertRaises(ConfigError):
            load_config(path)

    def test_unknown_key_warns(self) -> None:
        path = self.write("[general]\nlog_level = \"debug\"\nfoo = 1\n")
        cfg = load_config(path)
        self.assertEqual(cfg.general.log_level, "debug")
        self.assertTrue(any("foo" in warning for warning in cfg.warnings))

    def test_unknown_section_warns(self) -> None:
        path = self.write("[unknown]\nx = 1\n")
        cfg = load_config(path)
        self.assertTrue(any("unknown" in warning for warning in cfg.warnings))

    def test_env_override(self) -> None:
        path = self.write('[general]\nlanguage = "en"\n')
        with mock.patch.dict(os.environ, {"NETFIX_CONFIG": str(path)}):
            cfg = load_config()
        self.assertEqual(cfg.general.language, "en")
        self.assertEqual(cfg.source, path)


if __name__ == "__main__":
    unittest.main()
