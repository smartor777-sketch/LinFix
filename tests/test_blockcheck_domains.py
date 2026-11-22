from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from linfix import config
from linfix.config import BlockcheckConfig


class BlockcheckConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "config.toml"

    def test_defaults(self) -> None:
        cfg = config.load_config(self.path)
        self.assertEqual(
            cfg.blockcheck.domains, ("rutracker.org", "telegram.org")
        )

    def test_parsed_from_file(self) -> None:
        self.path.write_text(
            '[blockcheck]\ndomains = ["rutracker.org", "flibusta.is"]\n',
            encoding="utf-8",
        )
        cfg = config.load_config(self.path)
        self.assertEqual(cfg.blockcheck.domains, ("rutracker.org", "flibusta.is"))

    def test_empty_list_falls_back(self) -> None:
        self.path.write_text('[blockcheck]\ndomains = []\n', encoding="utf-8")
        cfg = config.load_config(self.path)
        self.assertEqual(
            cfg.blockcheck.domains, BlockcheckConfig().domains
        )

    def test_invalid_type_warns(self) -> None:
        self.path.write_text('[blockcheck]\ndomains = "one,two"\n', encoding="utf-8")
        cfg = config.load_config(self.path)
        self.assertEqual(cfg.blockcheck.domains, BlockcheckConfig().domains)
        self.assertTrue(any("blockcheck" in warning for warning in cfg.warnings))

    def test_write_new_section(self) -> None:
        text = '[guard]\nenabled = true\n'
        result = config.set_blockcheck_domains(
            text, ["rutracker.org", "telegram.org", "flibusta.is"]
        )
        self.assertIn("[blockcheck]", result)
        self.assertIn('domains = ["rutracker.org", "telegram.org", "flibusta.is"]', result)

    def test_replace_existing_key(self) -> None:
        text = '[blockcheck]\n# keep\ndomains = ["old.example"]\n'
        result = config.set_blockcheck_domains(text, ["rutracker.org"])
        self.assertIn("# keep", result)
        self.assertIn('domains = ["rutracker.org"]', result)
        self.assertNotIn("old.example", result)

    def test_rejects_empty(self) -> None:
        with self.assertRaises(ValueError):
            config.set_blockcheck_domains("[blockcheck]\n", [])

    def test_roundtrip(self) -> None:
        text = config.set_blockcheck_domains(
            "", ["rutracker.org", "flibusta.is"]
        )
        self.path.write_text(text, encoding="utf-8")
        cfg = config.load_config(self.path)
        self.assertEqual(cfg.blockcheck.domains, ("rutracker.org", "flibusta.is"))


if __name__ == "__main__":
    unittest.main()
