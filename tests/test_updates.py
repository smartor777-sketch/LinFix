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


class VersionParseTests(unittest.TestCase):
    def test_tuple(self) -> None:
        self.assertEqual(updates._version_tuple("v1.0.7"), (1, 0, 7))
        self.assertEqual(updates._version_tuple("v1.0.5.2"), (1, 0, 5, 2))
        self.assertEqual(updates._version_tuple("1.2"), (1, 2))

    def test_newer(self) -> None:
        self.assertTrue(updates._is_newer("v1.0.7", "v1.0.6"))
        self.assertTrue(updates._is_newer("v1.0.10", "v1.0.9"))
        self.assertFalse(updates._is_newer("v1.0.6", "v1.0.7"))
        self.assertFalse(updates._is_newer("v1.0.6", "v1.0.6"))
        self.assertFalse(updates._is_newer(None, "v1.0.6"))

    def test_newer_when_nothing_installed(self) -> None:
        self.assertTrue(updates._is_newer("v1.0.7", None))


class InstalledVersionTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_reads_version_file(self) -> None:
        version_file = self.root / "zapret-version"
        version_file.write_text("v1.0.7\n", encoding="utf-8")
        with mock.patch.object(updates, "ZAPRET_VERSION_FILE", version_file):
            self.assertEqual(updates.installed_zapret_version(), "v1.0.7")

    def test_falls_back_to_binary(self) -> None:
        """Если файла версии нет — парсим вывод nfqws2 --version."""

        version_file = self.root / "missing"
        install = self.root / "zapret2"
        (install / "nfq2").mkdir(parents=True)
        binary = install / "nfq2" / "nfqws2"
        binary.write_text("")

        from types import SimpleNamespace

        def fake_run(argv, **kwargs):
            if argv and str(argv[0]).endswith("nfqws2"):
                return SimpleNamespace(
                    returncode=0,
                    stdout="github version v1.0.6 (abc) lua_compat_ver 6\n",
                    stderr="",
                )
            return SimpleNamespace(returncode=0, stdout="", stderr="")

        info = SimpleNamespace(found=True, install_dir=str(install))
        with mock.patch.object(updates, "ZAPRET_VERSION_FILE", version_file):
            with mock.patch.object(updates.zapret, "detect_zapret", return_value=info):
                with mock.patch.object(updates.util, "run", side_effect=fake_run):
                    self.assertEqual(updates.installed_zapret_version(), "v1.0.6")

    def test_none_when_absent(self) -> None:
        from types import SimpleNamespace

        with mock.patch.object(updates, "ZAPRET_VERSION_FILE", self.root / "no"):
            with mock.patch.object(
                updates.zapret, "detect_zapret",
                return_value=SimpleNamespace(found=False, install_dir=None),
            ):
                self.assertIsNone(updates.installed_zapret_version())


class CompareWithReleaseTests(unittest.TestCase):
    def test_newer_release(self) -> None:
        remote = updates.Remote(zapret_tag="v1.0.7", tgws_tag="v1.12.0")
        with mock.patch.object(
            updates, "installed_zapret_version", return_value="v1.0.6"
        ):
            with mock.patch.object(updates.tgws, "is_installed", return_value=False):
                found = updates.compare(remote)
        self.assertTrue(found.has_zapret)
        self.assertEqual(found.zapret_new, "v1.0.7")

    def test_same_release_is_not_update(self) -> None:
        remote = updates.Remote(zapret_tag="v1.0.7", tgws_tag="v1.12.0")
        with mock.patch.object(
            updates, "installed_zapret_version", return_value="v1.0.7"
        ):
            with mock.patch.object(updates.tgws, "is_installed", return_value=False):
                found = updates.compare(remote)
        self.assertFalse(found.has_zapret)


if __name__ == "__main__":
    unittest.main()


class UpdateZapretUsesHelperTests(unittest.TestCase):
    """Бэкап пишется в /var/lib — значит только через helper, не напрямую.

    Прямой вызов падал с PermissionError, и пользователь видел в GUI
    «Не удалось обновить zapret2 — отказано в доступе».
    """

    def test_backup_goes_through_helper(self) -> None:
        from types import SimpleNamespace
        from linfix import api

        calls: list[tuple[str, list[str]]] = []

        def fake_helper(action, args, **kwargs):
            calls.append((action, list(args)))
            if action == "zapret.save_profile":
                return 0, {"ok": True, "files": ["config"], "install_dir": "/x"}
            if action == "zapret.install_easy":
                return 0, {"ok": True}
            if action == "zapret.restore_profile":
                return 0, {"ok": True, "restored": ["config"]}
            return 0, {"ok": True}

        remote = updates.Remote(
            zapret_tag="v1.0.7",
            zapret_asset="zapret2-v1.0.7.tar.gz",
        )
        with mock.patch.object(api.util, "run_helper", side_effect=fake_helper):
            with mock.patch.object(api, "restart_zapret", return_value=0):
                with mock.patch.object(api.updates, "fetch_remote", return_value=remote):
                    code, payload = api.update_zapret2(cfg=None)  # type: ignore[arg-type]

        actions = [action for action, _ in calls]
        self.assertIn("zapret.save_profile", actions)
        self.assertIn("zapret.restore_profile", actions)
        # Бэкап обязан идти ДО установки, иначе сохранять будет нечего.
        self.assertLess(
            actions.index("zapret.save_profile"),
            actions.index("zapret.install_easy"),
        )
        self.assertLess(
            actions.index("zapret.install_easy"),
            actions.index("zapret.restore_profile"),
        )
        self.assertEqual(code, 0)
        self.assertTrue(payload.get("ok"))

    def test_save_profile_failure_stops_update(self) -> None:
        from linfix import api

        def fake_helper(action, args, **kwargs):
            if action == "zapret.save_profile":
                return 5, {"ok": False, "error": "нет прав на /var/lib"}
            raise AssertionError("после неудачного бэкапа установку начинать нельзя")

        with mock.patch.object(api.util, "run_helper", side_effect=fake_helper):
            code, payload = api.update_zapret2(cfg=None)  # type: ignore[arg-type]

        self.assertEqual(code, api.EXIT_NO_RIGHTS)
        self.assertFalse(payload.get("ok"))
        self.assertIn("нет прав", str(payload.get("error")))


class ReportLinesTests(unittest.TestCase):
    """Отчёт по компонентам: видно, для чего обновление есть, а для чего нет."""

    def test_both_actual(self) -> None:
        found = updates.Found(
            zapret_current="v1.0.7", tgws_current="v1.12.0", tgws_new="v1.12.0"
        )
        lines = found.report_lines()
        self.assertEqual(len(lines), 2)
        self.assertIn("zapret2: обновлений нет", lines[0][0])
        self.assertIn("v1.0.7", lines[0][0])
        self.assertFalse(lines[0][1])
        self.assertIn("TgWsProxy: обновлений нет", lines[1][0])
        self.assertFalse(lines[1][1])

    def test_zapret_has_update(self) -> None:
        found = updates.Found(
            zapret_current="v1.0.7",
            zapret_new="v1.0.8",
            tgws_current="v1.12.0",
            tgws_new="v1.12.0",
        )
        lines = found.report_lines()
        self.assertIn("zapret2: есть обновление v1.0.8", lines[0][0])
        self.assertTrue(lines[0][1])
        # Второй компонент остаётся зелёным.
        self.assertFalse(lines[1][1])

    def test_tgws_has_update(self) -> None:
        found = updates.Found(
            zapret_current="v1.0.7",
            tgws_current="v1.12.0",
            tgws_new="v1.13.0",
        )
        lines = found.report_lines()
        self.assertFalse(lines[0][1])
        self.assertIn("TgWsProxy: есть обновление v1.13.0", lines[1][0])
        self.assertTrue(lines[1][1])

    def test_not_installed(self) -> None:
        found = updates.Found()
        lines = found.report_lines()
        self.assertIn("не установлен", lines[0][0])
        self.assertIn("не установлен", lines[1][0])
