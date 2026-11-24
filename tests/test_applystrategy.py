from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from linfix import applystrategy


CONFIG = '''NFQWS2_OPT="
--filter-tcp=443 --filter-l7=tls --hostlist=/opt/zapret2/ipset/linfix-cf-hosts.txt --payload=tls_client_hello --lua-desync=fake:blob=fake_default_tls --new
--filter-tcp=80 --filter-l7=http <HOSTLIST> --payload=http_req --lua-desync=http_methodeol --new
"

NFQWS2_PORTS_TCP=80,443
'''


class BuildLineTests(unittest.TestCase):
    def test_adds_filters(self) -> None:
        line = applystrategy.build_profile_line(
            ["--lua-desync=multisplit:pos=2"], hostlist="/x/hosts.txt"
        )
        self.assertIn("--hostlist=/x/hosts.txt", line)
        self.assertIn("--filter-tcp=443", line)
        self.assertIn("--payload=tls_client_hello", line)
        self.assertTrue(line.endswith("--new"))

    def test_no_duplicate_payload(self) -> None:
        """blockcheck2 уже передаёт --payload: дублировать нельзя."""

        line = applystrategy.build_profile_line(
            ["--payload=tls_client_hello", "--lua-desync=fake"], hostlist="/x"
        )
        self.assertEqual(line.count("--payload="), 1)

    def test_empty_raises(self) -> None:
        with self.assertRaises(applystrategy.ApplyError):
            applystrategy.build_profile_line([], hostlist="/x")


class BlockTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = Path(self._tmp.name) / "config"
        self.cfg.write_text(CONFIG, encoding="utf-8")

    def test_read_block(self) -> None:
        block = applystrategy.read_profile_block(self.cfg)
        self.assertIn("linfix-cf-hosts.txt", block)
        self.assertNotIn("NFQWS2_PORTS_TCP", block)

    def test_replace_puts_candidate_first(self) -> None:
        line = applystrategy.build_profile_line(
            ["--lua-desync=test"], hostlist="/x"
        )
        previous = applystrategy.replace_profile_block(line, self.cfg)
        block = applystrategy.read_profile_block(self.cfg)
        self.assertTrue(block.splitlines()[0].startswith("--filter-tcp=443"))
        self.assertIn("test", block.splitlines()[0])
        # Прежние профили сохранены
        self.assertIn("http_methodeol", block)
        self.assertIn("fake:blob=fake_default_tls", block)
        # Откат возвращает ровно то же
        applystrategy.restore_profile_block(previous, self.cfg)
        self.assertEqual(applystrategy.read_profile_block(self.cfg), previous)

    def test_restore_is_exact(self) -> None:
        before = applystrategy.read_profile_block(self.cfg)
        line = applystrategy.build_profile_line(["--lua-desync=x"], hostlist="/h")
        previous = applystrategy.replace_profile_block(line, self.cfg)
        applystrategy.restore_profile_block(previous, self.cfg)
        self.assertEqual(applystrategy.read_profile_block(self.cfg), before)

    def test_hostlist_from_config(self) -> None:
        self.assertEqual(
            applystrategy.hostlist_for_test(self.cfg),
            "/opt/zapret2/ipset/linfix-cf-hosts.txt",
        )

    def test_missing_block_raises(self) -> None:
        broken = Path(self._tmp.name) / "broken"
        broken.write_text("NFQWS2_PORTS_TCP=443\n", encoding="utf-8")
        with self.assertRaises(applystrategy.ApplyError):
            applystrategy.read_profile_block(broken)


class AppliedContextTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.cfg = Path(self._tmp.name) / "config"
        self.cfg.write_text(CONFIG, encoding="utf-8")
        self.before = applystrategy.read_profile_block(self.cfg)

    def test_restores_on_success(self) -> None:
        with mock.patch.object(applystrategy, "restart", return_value=True):
            with applystrategy.applied(["--lua-desync=x"], config_path=self.cfg) as line:
                self.assertIn("--lua-desync=x", line)
                self.assertIn("--lua-desync=x", applystrategy.read_profile_block(self.cfg))
        self.assertEqual(applystrategy.read_profile_block(self.cfg), self.before)

    def test_restores_on_exception(self) -> None:
        """Откат обязателен даже при исключении внутри блока."""

        with mock.patch.object(applystrategy, "restart", return_value=True):
            with self.assertRaises(RuntimeError):
                with applystrategy.applied(["--lua-desync=x"], config_path=self.cfg):
                    raise RuntimeError("сбой проверки")
        self.assertEqual(applystrategy.read_profile_block(self.cfg), self.before)

    def test_rollback_when_service_not_started(self) -> None:
        """Служба не поднялась — профиль возвращаем, не оставляем чужой."""

        with mock.patch.object(applystrategy, "restart", return_value=False):
            with self.assertRaises(applystrategy.ApplyError):
                with applystrategy.applied(["--lua-desync=x"], config_path=self.cfg):
                    pass
        self.assertEqual(applystrategy.read_profile_block(self.cfg), self.before)


if __name__ == "__main__":
    unittest.main()
