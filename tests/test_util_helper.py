"""Тесты выбора команды helper (пакет установлен / dev-режим)."""

from __future__ import annotations

import sys
import unittest
from unittest.mock import patch

from linfix import util


class DevHelperFallbackTests(unittest.TestCase):
    def test_uses_pkexec_with_ascii_src_pythonpath(self) -> None:
        """ASCII-путь передаётся через env, как раньше."""

        ascii_root = "/opt/linfix/src"
        with patch.object(
            util.Path, "resolve", return_value=util.Path(ascii_root + "/linfix/util.py")
        ), patch.object(
            util, "which", side_effect=lambda name: "/usr/bin/pkexec" if name == "pkexec" else None
        ):
            cmd = util._dev_helper_command("zapret.set_enabled", ["--service", "zapret2"])

        self.assertEqual(cmd[0], "pkexec")
        self.assertIn("env", cmd)
        self.assertIn(f"PYTHONPATH={ascii_root}", cmd)
        tail = ["linfix.helper", "zapret.set_enabled", "--service", "zapret2", "--json"]
        self.assertEqual(cmd[-5:], tail)
        # --json должен быть ровно один: в dev-ветке его добавляет
        # _dev_helper_command, run_helper его больше не дублирует.
        self.assertEqual(cmd.count("--json"), 1)

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


class CyrillicPathTests(unittest.TestCase):
    def test_ascii_safe(self) -> None:
        self.assertTrue(util._ascii_safe("/opt/linfix"))
        self.assertFalse(util._ascii_safe("/home/alex/Документы/LinFix"))

    def test_non_ascii_path_uses_ascii_wrapper(self) -> None:
        """pkexec падает на не-ASCII argv — путь должен уйти в файл-обёртку."""

        cyrillic = "/home/alex/Документы/LinFix/src"
        with patch.object(util.Path, "resolve") as resolve_mock:
            resolve_mock.return_value.parent.parent = cyrillic  # type: ignore[attr-defined]
            with patch.object(util.Path, "resolve", return_value=util.Path(cyrillic + "/linfix/util.py")):
                with patch.object(util, "which", side_effect=lambda n: "/usr/bin/pkexec" if n == "pkexec" else None):
                    cmd = util._dev_helper_command("zapret.set_enabled", ["--service", "zapret2"])

        self.assertEqual(cmd[0], "pkexec")
        self.assertTrue(all(util._ascii_safe(part) for part in cmd), cmd)
        wrapper = util.Path(cmd[1])
        self.assertEqual(wrapper.name, "linfix-helper")
        text = wrapper.read_text(encoding="utf-8")
        self.assertIn(cyrillic, text, "путь к исходникам должен быть внутри файла")
        self.assertIn("zapret.set_enabled", text)

    def test_ascii_path_uses_env(self) -> None:
        with patch.object(util.Path, "resolve", return_value=util.Path("/opt/linfix/src/linfix/util.py")):
            with patch.object(util, "which", side_effect=lambda n: "/usr/bin/pkexec" if n == "pkexec" else None):
                cmd = util._dev_helper_command("zapret.stop", ["--service", "zapret2"])
        self.assertEqual(cmd[:2], ["pkexec", "env"])
        self.assertIn("PYTHONPATH=/opt/linfix/src", cmd)


class AuthTimeoutTests(unittest.TestCase):
    def test_timeout_becomes_readable_error(self) -> None:
        """Ожидание пароля не должно вылетать CommandError наружу."""

        with patch.object(util, "is_root", return_value=False), patch.dict(
            "os.environ", {"LINFIX_HELPER": "pkexec test"}, clear=False
        ), patch.object(
            util, "run", side_effect=util.CommandError(["pkexec"], 124, "", "timeout")
        ):
            with self.assertRaises(util.NetfixError) as ctx:
                util.run_helper("zapret.stop", ["--service", "zapret2"], timeout=1)
        self.assertIn("парол", str(ctx.exception))
