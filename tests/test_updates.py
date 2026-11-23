from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

from linfix import updates


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class CompareTests(unittest.TestCase):
    def test_no_updates(self) -> None:
        now = utc_now()
        found = updates.Found(
            zapret_current=now.isoformat()[:10],
            tgws_current="v1.0.0",
            tgws_new="v1.0.0",
        )
        self.assertFalse(found.any)
        self.assertFalse(found.has_zapret)
        self.assertFalse(found.has_tgws)

    def test_tgws_newer(self) -> None:
        found = updates.Found(tgws_current="v1.0.0", tgws_new="v1.1.0")
        self.assertTrue(found.any)
        self.assertTrue(found.has_tgws)
        self.assertIn("TgWsProxy v1.1.0", found.summary())

    def test_zapret_newer(self) -> None:
        found = updates.Found(zapret_current="abc", zapret_new="def")
        self.assertTrue(found.has_zapret)
        self.assertIn("zapret2 def", found.summary())

    def test_summary_both(self) -> None:
        found = updates.Found(
            zapret_current="a", zapret_new="b", tgws_current="v1", tgws_new="v2"
        )
        self.assertEqual(found.summary(), "zapret2 b, TgWsProxy v2")


class CacheTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)

    def test_save_and_load(self) -> None:
        found = updates.Found(
            zapret_current="2026-11-04",
            zapret_new="4362c83a",
            tgws_current="v1.12.0",
            tgws_new="v1.13.0",
            checked_at="2026-11-23T10:00:00+03:00",
        )
        updates.save_found(found, home=self.home)
        back = updates.load_found(home=self.home)
        self.assertEqual(back.zapret_new, "4362c83a")
        self.assertEqual(back.tgws_new, "v1.13.0")
        self.assertTrue(back.has_zapret)
        self.assertTrue(back.has_tgws)

    def test_empty_cache(self) -> None:
        found = updates.load_found(home=self.home)
        self.assertFalse(found.any)
        self.assertIsNone(found.checked_at)

    def test_cache_is_in_user_dir(self) -> None:
        path = updates.cache_path(self.home)
        self.assertEqual(path.parent, self.home / ".local" / "state" / "linfix")

    def test_due_when_empty(self) -> None:
        self.assertTrue(updates.is_due(home=self.home))

    def test_not_due_after_check(self) -> None:
        updates.save_found(
            updates.Found(checked_at=utc_now().isoformat()), home=self.home
        )
        self.assertFalse(updates.is_due(home=self.home))

    def test_due_after_interval(self) -> None:
        old = utc_now() - timedelta(days=2)
        updates.save_found(updates.Found(checked_at=old.isoformat()), home=self.home)
        self.assertTrue(updates.is_due(home=self.home))

    def test_bad_timestamp_means_due(self) -> None:
        updates.save_found(updates.Found(checked_at="не дата"), home=self.home)
        self.assertTrue(updates.is_due(home=self.home))


class ProfileBackupTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.zapret = self.root / "zapret2"
        (self.zapret / "ipset").mkdir(parents=True)
        (self.zapret / "config").write_text('NFQWS2_OPT="\n--lua-desync=fake\n"\n')
        (self.zapret / "ipset" / "linfix-cf-hosts.txt").write_text("kws1.a.co.uk\n")
        (self.zapret / "ipset" / "zapret-hosts-rut.txt").write_text("rutracker.org\n")
        self.backup = self.root / "backup"

    def test_save_and_restore(self) -> None:
        with mock.patch.object(updates, "BACKUP_DIR", self.backup):
            meta = updates.save_profile(self.zapret)
            self.assertIn("config", meta["files"])
            self.assertIn("ipset/linfix-cf-hosts.txt", meta["files"])

            # install_easy.sh затирает конфиг и списки
            (self.zapret / "config").write_text("# затёрто\n")
            (self.zapret / "ipset" / "linfix-cf-hosts.txt").write_text("")

            result = updates.restore_profile(self.zapret)
            self.assertIn("config", result["restored"])
            self.assertIn("lua-desync", (self.zapret / "config").read_text())
            self.assertEqual(
                (self.zapret / "ipset" / "linfix-cf-hosts.txt").read_text().strip(),
                "kws1.a.co.uk",
            )

    def test_save_requires_config(self) -> None:
        (self.zapret / "config").unlink()
        with mock.patch.object(updates, "BACKUP_DIR", self.backup):
            with self.assertRaises(updates.UpdateError):
                updates.save_profile(self.zapret)

    def test_restore_without_backup(self) -> None:
        with mock.patch.object(updates, "BACKUP_DIR", self.backup):
            with self.assertRaises(updates.UpdateError):
                updates.restore_profile(self.zapret)

    def test_missing_optional_files_skipped(self) -> None:
        (self.zapret / "ipset" / "zapret-hosts-rut.txt").unlink()
        with mock.patch.object(updates, "BACKUP_DIR", self.backup):
            meta = updates.save_profile(self.zapret)
        self.assertNotIn("ipset/zapret-hosts-rut.txt", meta["files"])


class InstalledMarkerTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_marker_from_binary(self) -> None:
        (self.root / "nfq2").mkdir()
        binary = self.root / "nfq2" / "nfqws2"
        binary.write_text("x")
        marker = updates.installed_zapret_marker(self.root)
        self.assertIsNotNone(marker)
        self.assertIn("-", marker or "")

    def test_marker_missing(self) -> None:
        self.assertIsNone(updates.installed_zapret_marker(self.root))


class CompareZapretTests(unittest.TestCase):
    def test_new_commit_after_install(self) -> None:
        installed = (utc_now() - timedelta(days=10)).isoformat()
        remote = updates.Remote(
            zapret_commit="4362c83a",
            zapret_date=utc_now().isoformat().replace("+00:00", "Z"),
            tgws_tag="v1.12.0",
        )
        with mock.patch.object(updates, "installed_zapret_marker", return_value=installed):
            with mock.patch.object(
                updates.tgws, "is_installed", return_value=False
            ):
                found = updates.compare(remote)
        self.assertTrue(found.has_zapret)
        self.assertEqual(found.zapret_new, "4362c83a")

    def test_old_commit_is_not_update(self) -> None:
        installed = utc_now().isoformat()
        remote = updates.Remote(
            zapret_commit="aaa",
            zapret_date=(utc_now() - timedelta(days=5)).isoformat().replace("+00:00", "Z"),
        )
        with mock.patch.object(updates, "installed_zapret_marker", return_value=installed):
            with mock.patch.object(updates.tgws, "is_installed", return_value=False):
                found = updates.compare(remote)
        self.assertFalse(found.has_zapret)


if __name__ == "__main__":
    unittest.main()
