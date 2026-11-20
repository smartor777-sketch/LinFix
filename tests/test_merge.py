from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path

from netfix import cfdomains

DOMAINS = ["pclead.co.uk", "offshor.co.uk", "cakeisalie.co.uk"]


class MergeTextTests(unittest.TestCase):
    def test_empty_file(self) -> None:
        result = cfdomains.merge_hostlist("", DOMAINS, today="2026-11-20")
        self.assertTrue(result.changed)
        self.assertEqual(result.added, tuple(DOMAINS))
        self.assertIn("# netfix: tgws cf domains (updated 2026-11-20)", result.text)
        self.assertEqual(result.newline, "\n")

    def test_idempotent(self) -> None:
        first = cfdomains.merge_hostlist("", DOMAINS, today="2026-11-20")
        second = cfdomains.merge_hostlist(first.text, DOMAINS, today="2026-12-01")
        self.assertFalse(second.changed)
        self.assertEqual(second.text, first.text)
        self.assertEqual(second.added, ())

    def test_preserves_comments_and_existing_lines(self) -> None:
        text = "# my own comment\nexample.com\nother.example\n"
        result = cfdomains.merge_hostlist(text, DOMAINS, today="2026-11-20")
        self.assertIn("# my own comment", result.text)
        self.assertIn("example.com", result.text)
        self.assertIn("other.example", result.text)

    def test_never_removes_old_domains(self) -> None:
        text = "old.example\n" + "\n".join(DOMAINS) + "\n"
        result = cfdomains.merge_hostlist(text, ["new.example.com"])
        self.assertTrue(result.changed)
        self.assertEqual(result.added, ("new.example.com",))
        self.assertIn("old.example", result.text)
        self.assertIn("pclead.co.uk", result.text)

    def test_crlf_is_preserved(self) -> None:
        text = "# comment\r\nexample.com\r\n"
        result = cfdomains.merge_hostlist(text, DOMAINS, today="2026-11-20")
        self.assertTrue(result.changed)
        self.assertEqual(result.newline, "\r\n")
        self.assertIn("\r\n", result.text)
        self.assertNotIn("\n", result.text.replace("\r\n", ""))

    def test_lf_is_preserved(self) -> None:
        text = "# comment\nexample.com\n"
        result = cfdomains.merge_hostlist(text, DOMAINS, today="2026-11-20")
        self.assertEqual(result.newline, "\n")
        self.assertNotIn("\r", result.text)

    def test_present_is_case_insensitive(self) -> None:
        text = "PCLEAD.CO.UK.\n"
        result = cfdomains.merge_hostlist(text, ["pclead.co.uk"])
        self.assertFalse(result.changed)
        self.assertEqual(result.present, ("pclead.co.uk",))

    def test_marker_is_not_duplicated(self) -> None:
        text = "# netfix: tgws cf domains (updated 2026-01-01)\nold.example\n"
        result = cfdomains.merge_hostlist(text, ["pclead.co.uk"], today="2026-11-20")
        self.assertEqual(result.text.count("# netfix: tgws cf domains"), 1)
        lines = result.text.splitlines()
        marker_index = next(
            index for index, line in enumerate(lines) if line.startswith("# netfix: tgws cf domains")
        )
        self.assertEqual(lines[marker_index + 1], "pclead.co.uk")

    def test_missing_trailing_newline(self) -> None:
        result = cfdomains.merge_hostlist("example.com", ["pclead.co.uk"], today="2026-11-20")
        self.assertTrue(result.text.startswith("example.com\n"))
        self.assertTrue(result.text.endswith("\n"))

    def test_normalizes_and_dedupes_domains(self) -> None:
        result = cfdomains.merge_hostlist("", [" PCLEAD.CO.UK ", "pclead.co.uk"], today="2026-11-20")
        self.assertEqual(result.added, ("pclead.co.uk",))
        self.assertEqual(result.total, ("pclead.co.uk",))
        self.assertEqual(result.text.count("pclead.co.uk"), 1)

    def test_invalid_domain_raises(self) -> None:
        with self.assertRaises(ValueError):
            cfdomains.merge_hostlist("", ["bad_domain"])

    def test_detect_newline(self) -> None:
        self.assertEqual(cfdomains.detect_newline(""), "\n")
        self.assertEqual(cfdomains.detect_newline("a\nb\n"), "\n")
        self.assertEqual(cfdomains.detect_newline("a\r\nb\r\n"), "\r\n")
        self.assertEqual(cfdomains.detect_newline("a\r\nb\r\nc\n"), "\r\n")
        self.assertEqual(cfdomains.detect_newline("a\r\nb\nc\n"), "\n")


class MergeFileTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_creates_file_without_backup(self) -> None:
        path = self.root / "sub" / "zapret-hosts-user.txt"
        result, backup = cfdomains.merge_hostlist_file(path, DOMAINS)
        self.assertTrue(result.changed)
        self.assertIsNone(backup)
        self.assertTrue(path.exists())
        self.assertIn("pclead.co.uk", path.read_text(encoding="utf-8"))

    def test_backup_only_when_changed(self) -> None:
        path = self.root / "zapret-hosts-user.txt"
        path.write_text("example.com\n", encoding="utf-8")

        result, backup = cfdomains.merge_hostlist_file(path, DOMAINS)
        self.assertTrue(result.changed)
        self.assertIsNotNone(backup)
        self.assertTrue(backup.exists())
        self.assertEqual(backup.read_text(encoding="utf-8"), "example.com\n")

        second, second_backup = cfdomains.merge_hostlist_file(path, DOMAINS)
        self.assertFalse(second.changed)
        self.assertIsNone(second_backup)
        backups = list(self.root.glob("zapret-hosts-user.txt.bak-netfix-*"))
        self.assertEqual(len(backups), 1)

    def test_preserves_crlf_and_mode(self) -> None:
        path = self.root / "zapret-hosts-user.txt"
        path.write_bytes(b"# comment\r\nexample.com\r\n")
        path.chmod(0o640)

        cfdomains.merge_hostlist_file(path, DOMAINS)

        data = path.read_bytes()
        self.assertIn(b"\r\n", data)
        self.assertNotIn(b"\n", data.replace(b"\r\n", b""))
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o640)

    def test_invalid_domain_does_not_touch_file(self) -> None:
        path = self.root / "zapret-hosts-user.txt"
        path.write_text("example.com\n", encoding="utf-8")
        with self.assertRaises(ValueError):
            cfdomains.merge_hostlist_file(path, ["bad_domain"])
        self.assertEqual(path.read_text(encoding="utf-8"), "example.com\n")


if __name__ == "__main__":
    unittest.main()
