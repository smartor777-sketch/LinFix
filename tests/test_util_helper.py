"""Тесты выбора команды helper (пакет установлен / dev-режим)."""

from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from linfix import util


class DevHelperFallbackTests(unittest.TestCase):
    def test_uses_pkexec_with_src_pythonpath(self) -> None:
        with patch.object(util, "which", side_effect=lambda name: "/usr/bin/pkexec" if name == "pkexec" else None):
            cmd = util._dev_helper_command(
                "zapret.set_enabled", ["--service", "zapret2", "--enabled", "on"]
            )

        self.assertEqual(cmd[0], "pkexec")
        self.assertIn("env", cmd)
        pythonpath = next(part for part in cmd if part.startswith("PYTHONPATH="))
        self.assertTrue(pythonpath.endswith("src"), pythonpath)
        tail = ["linfix.helper", "zapret.set_enabled", "--service", "zapret2", "--enabled", "on"]
        self.assertEqual(cmd[-6:], tail)
        # --json добавляет только run_helper, не сама команда.
        self.assertNotIn("--json", cmd)
        self.assertIn(sys.executable, cmd)

    def test_without_escalation_raises(self) -> None:
        with patch.object(util, "which", return_value=None):
            with self.assertRaises(util.NetfixError):
                util._dev_helper_command("zapret.stop", ["--service", "zapret2"])

    def test_run_helper_prefers_installed_binary(self) -> None:
        completed = type(
            "CP", (), {"returncode": 0, "stdout": '{"ok": true}', "stderr": ""}
        )()
        with patch.object(util, "is_root", return_value=False), patch.dict(
            "os.environ", {}, clear=False
        ), patch.object(util, "which", return_value=None), patch.object(
            util, "_dev_helper_command", return_value=["pkexec", "dev"]
        ) as dev, patch.object(util, "run", return_value=completed):
            os_environ = util.os.environ
            os_environ.pop("LINFIX_HELPER", None)
            code, payload = util.run_helper("zapret.stop", ["--service", "zapret2"])

        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        dev.assert_called_once_with("zapret.stop", ["--service", "zapret2"])


if __name__ == "__main__":
    unittest.main()


class JsonParsingTests(unittest.TestCase):
    def test_stdout_preferred_over_glib_noise(self) -> None:
        """GLib-шум в stderr не должен перебивать валидный JSON из stdout."""

        stdout = '{\n  "ok": true,\n  "service": "zapret2"\n}\n'
        stderr = (
            "\n(process:1): GLib-CRITICAL **: g_variant_new_string: "
            "assertion failed\n"
        )
        payload = util._parse_helper_payload(stdout, stderr)
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["service"], "zapret2")

    def test_json_surrounded_by_noise(self) -> None:
        text = 'предупреждение\n{"ok": false, "error": "boom"}\nещё текст\n'
        payload = util._parse_helper_payload(text, "")
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["error"], "boom")

    def test_empty_streams(self) -> None:
        self.assertEqual(util._parse_helper_payload("", ""), {})

    def test_dev_helper_has_single_json_flag(self) -> None:
        """--json добавляет только run_helper, иначе парсер получает мусор."""

        cmd = util._dev_helper_command("zapret.set_enabled", ["--service", "zapret2"])
        self.assertNotIn("--json", cmd)
