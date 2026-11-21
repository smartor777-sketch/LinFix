"""Тесты hostlist: разбор, добавление/удаление, сохранение чужих строк."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from linfix import hostlist


class ParseTests(unittest.TestCase):
    def test_plain_lines(self) -> None:
        text = "a.com\nb.com\n# comment\n// comment\n\n"
        self.assertEqual(hostlist.parse_list_text(text), ["a.com", "b.com"])

    def test_urls_and_separators(self) -> None:
        text = "https://d.com/path\nE.COM, f.com; g.com\t h.com"
        self.assertEqual(
            hostlist.parse_list_text(text), ["d.com", "e.com", "f.com", "g.com", "h.com"]
        )

    def test_deduplicates(self) -> None:
        self.assertEqual(hostlist.parse_list_text("a.com\nA.com\n"), ["a.com"])

    def test_rejects_garbage(self) -> None:
        self.assertEqual(hostlist.parse_list_text("не домен\na.com"), ["a.com"])

    def test_normalize(self) -> None:
        self.assertEqual(hostlist.normalize_domain(" HTTPS://WWW.Example.COM./x "), "www.example.com")
        self.assertIsNone(hostlist.normalize_domain("localhost"))
        self.assertIsNone(hostlist.normalize_domain(""))


class FileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "hostlist.txt"

    def test_read_entries_marks_sources(self) -> None:
        self.path.write_text(
            "foreign.com\n"
            "# linfix: tgws cf domains (updated 2026-01-01)\n"
            "pclead.co.uk\n"
            "# linfix: user domains\n"
            "mine.com\n",
            encoding="utf-8",
        )
        entries = {e.domain: e.source for e in hostlist.read_entries(self.path)}
        self.assertEqual(entries, {"foreign.com": "other", "pclead.co.uk": "cf", "mine.com": "user"})

    def test_add_keeps_foreign_lines(self) -> None:
        self.path.write_text("foreign.com\nother.org\n", encoding="utf-8")
        result = hostlist.add_domains(self.path, ["mine.com", "foreign.com"])
        self.assertEqual(result.added, ["mine.com"])
        self.assertEqual(result.skipped, ["foreign.com"])
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("foreign.com", text)
        self.assertIn("other.org", text)
        self.assertIn(hostlist.USER_MARKER, text)
        self.assertIn("mine.com", text)

    def test_add_is_idempotent(self) -> None:
        self.path.write_text("foreign.com\n", encoding="utf-8")
        hostlist.add_domains(self.path, ["mine.com"])
        before = self.path.read_text(encoding="utf-8")
        result = hostlist.add_domains(self.path, ["mine.com"])
        self.assertEqual(result.added, [])
        self.assertEqual(self.path.read_text(encoding="utf-8"), before)

    def test_remove_only_linfix_blocks(self) -> None:
        self.path.write_text(
            "foreign.com\n"
            "# linfix: tgws cf domains (updated 2026-01-01)\n"
            "cf.com\n"
            "# linfix: user domains\n"
            "mine.com\n",
            encoding="utf-8",
        )
        result = hostlist.remove_domains(self.path, ["mine.com", "foreign.com"])
        self.assertEqual(result.added, ["mine.com"])
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("foreign.com", text)
        self.assertNotIn("mine.com", text)
        self.assertIn("cf.com", text)

    def test_backup_created_on_change(self) -> None:
        self.path.write_text("foreign.com\n", encoding="utf-8")
        hostlist.add_domains(self.path, ["mine.com"])
        backups = list(Path(self._tmp.name).glob("*.bak-linfix-*"))
        self.assertTrue(backups)

    def test_fetch_rejects_http(self) -> None:
        with self.assertRaises(hostlist.HostlistError):
            hostlist.fetch_list("http://example.com/list.txt")


class SourceTests(unittest.TestCase):
    def test_lists_known_scripts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            (directory / "get_antizapret_domains.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            (directory / "unknown.sh").write_text("#!/bin/sh\n", encoding="utf-8")
            found = hostlist.list_sources(directory)
        self.assertEqual([name for name, _ in found], ["get_antizapret_domains.sh"])


if __name__ == "__main__":
    unittest.main()


class CfSectionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "hostlist.txt"
        self.path.write_text(
            "foreign.com\n# linfix: tgws cf domains (updated 2026-01-01)\npclead.co.uk\n",
            encoding="utf-8",
        )

    def test_cf_domains_reads_only_cf_block(self) -> None:
        self.assertEqual(hostlist.cf_domains(self.path), ["pclead.co.uk"])

    def test_add_to_cf_section(self) -> None:
        result = hostlist.add_domains(self.path, ["new.co.uk"], target_section="cf")
        self.assertEqual(result.added, ["new.co.uk"])
        self.assertIn("new.co.uk", hostlist.cf_domains(self.path))
        self.assertIn("foreign.com", self.path.read_text(encoding="utf-8"))

    def test_add_to_user_section_by_default(self) -> None:
        hostlist.add_domains(self.path, ["mine.com"])
        self.assertNotIn("mine.com", hostlist.cf_domains(self.path))
        text = self.path.read_text(encoding="utf-8")
        self.assertIn(hostlist.USER_MARKER, text)
