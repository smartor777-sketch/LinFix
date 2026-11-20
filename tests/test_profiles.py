from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from linfix import profiles

TOML = """
[profiles."11111111-1111-1111-1111-111111111111"]
name = "Домашний Wi-Fi"
strategy = "general"

[profiles."22222222-2222-2222-2222-222222222222"]
name = "Работа"
strategy = "work"
"""


class ValidationTests(unittest.TestCase):
    UUID = "11111111-2222-3333-4444-555555555555"

    def test_uuid_valid(self) -> None:
        self.assertEqual(profiles.validate_uuid(self.UUID), self.UUID)

    def test_uuid_case_insensitive(self) -> None:
        self.assertEqual(profiles.validate_uuid(self.UUID.lower()), self.UUID.lower())

    def test_uuid_invalid(self) -> None:
        for value in ("", "nope", "11111111-2222-3333-4444-55555555555"):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    profiles.validate_uuid(value)

    def test_name(self) -> None:
        self.assertEqual(profiles.validate_name(" Дом "), "Дом")
        with self.assertRaises(ValueError):
            profiles.validate_name("")

    def test_strategy(self) -> None:
        self.assertEqual(profiles.validate_strategy("general"), "general")
        with self.assertRaises(ValueError):
            profiles.validate_strategy("cf")


class NmcliParseTests(unittest.TestCase):
    def test_parse(self) -> None:
        output = "UUID1:Wi-Fi:ethernet\nUUID2:Мобильный:lte\n"
        connections = profiles.parse_nmcli_output(output)
        self.assertEqual(len(connections), 2)
        self.assertEqual(connections[0].uuid, "UUID1")
        self.assertEqual(connections[0].name, "Wi-Fi")
        self.assertEqual(connections[1].ctype, "lte")

    def test_current_uuid_with_nmcli(self) -> None:
        with (
            mock.patch.object(profiles.util, "which", return_value="/usr/bin/nmcli"),
            mock.patch.object(
                profiles.util,
                "run",
                return_value=SimpleNamespace(stdout="UUID:a:b\nUUID2:c:d\n", returncode=0),
            ),
        ):
            self.assertEqual(profiles.current_uuid(), "UUID")

    def test_current_uuid_without_nmcli(self) -> None:
        with mock.patch.object(profiles.util, "which", return_value=None):
            self.assertIsNone(profiles.current_uuid())


class SetClearProfileTests(unittest.TestCase):
    UUID = "11111111-2222-3333-4444-555555555555"

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "profiles.toml"

    def test_set_and_save_roundtrip(self) -> None:
        result = profiles.set_profile(self.UUID, "Дом", "general", path=self.path)
        self.assertTrue(result["changed"])
        loaded = profiles.load_profiles(self.path)
        self.assertEqual(loaded[self.UUID].name, "Дом")
        self.assertEqual(loaded[self.UUID].strategy, "general")
        self.assertTrue(self.path.exists())

    def test_set_existing_no_change(self) -> None:
        profiles.set_profile(self.UUID, "Дом", path=self.path)
        result = profiles.set_profile(self.UUID, "Дом", path=self.path)
        self.assertFalse(result["changed"])

    def test_set_invalid_rejected(self) -> None:
        with self.assertRaises(ValueError):
            profiles.set_profile("bad", "x", path=self.path)

    def test_clear_profile(self) -> None:
        profiles.set_profile(self.UUID, "Дом", path=self.path)
        result = profiles.clear_profile(self.UUID, path=self.path)
        self.assertTrue(result["changed"])
        self.assertNotIn(self.UUID, profiles.load_profiles(self.path))

    def test_clear_missing_is_noop(self) -> None:
        result = profiles.clear_profile(self.UUID, path=self.path)
        self.assertFalse(result["changed"])

    def test_save_format(self) -> None:
        profiles.set_profile(self.UUID, "Дом", path=self.path)
        text = self.path.read_text(encoding="utf-8")
        self.assertIn(f'[profiles."{self.UUID}"]', text)
        self.assertIn("name = \"Дом\"", text)
        self.assertIn("strategy = \"general\"", text)


class ProfileTests(unittest.TestCase):
    def test_load(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "profiles.toml"
            path.write_text(TOML, encoding="utf-8")
            loaded = profiles.load_profiles(path)
        self.assertEqual(len(loaded), 2)
        self.assertEqual(
            loaded["11111111-1111-1111-1111-111111111111"].name,
            "Домашний Wi-Fi",
        )
        self.assertEqual(loaded["22222222-2222-2222-2222-222222222222"].strategy, "work")

    def test_missing_file(self) -> None:
        self.assertEqual(profiles.load_profiles("/nonexistent/profiles.toml"), {})

    def test_resolve_known(self) -> None:
        loaded = {
            "uuid": profiles.Profile(name="Дом", strategy="general"),
        }
        self.assertEqual(profiles.resolve_profile(loaded, "uuid").name, "Дом")

    def test_resolve_missing_falls_back_to_general(self) -> None:
        resolved = profiles.resolve_profile({}, "unknown-uuid")
        self.assertEqual(resolved.name, "general")
        self.assertEqual(resolved.strategy, "general")
        self.assertEqual(profiles.resolve_profile({}, None).name, "general")


if __name__ == "__main__":
    unittest.main()
