from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from netfix import profiles

TOML = """
[profiles."11111111-1111-1111-1111-111111111111"]
name = "Домашний Wi-Fi"
strategy = "general"

[profiles."22222222-2222-2222-2222-222222222222"]
name = "Работа"
strategy = "work"
"""


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
