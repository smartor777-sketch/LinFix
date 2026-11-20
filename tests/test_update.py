from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from netfix import cli, config, state


class DomainsDueTests(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = config.Config()
        self.now = datetime(2026, 11, 20, 12, 0, tzinfo=timezone.utc)

    def test_never_updated(self) -> None:
        self.assertTrue(cli._domains_due(self.cfg, state.State(), now=self.now))

    def test_recent_update(self) -> None:
        updated = (self.now - timedelta(days=1)).isoformat()
        self.assertFalse(
            cli._domains_due(self.cfg, state.State(domains_updated_at=updated), now=self.now)
        )

    def test_old_update(self) -> None:
        updated = (self.now - timedelta(days=self.cfg.lists.update_days)).isoformat()
        self.assertTrue(
            cli._domains_due(self.cfg, state.State(domains_updated_at=updated), now=self.now)
        )

    def test_bad_date(self) -> None:
        self.assertTrue(
            cli._domains_due(
                self.cfg, state.State(domains_updated_at="not-a-date"), now=self.now
            )
        )


class ArgsFactoryTests(unittest.TestCase):
    def test_domains_args(self) -> None:
        args = cli._domains_update_args(dry_run=True, force=True, offline=True)
        self.assertTrue(args.dry_run)
        self.assertTrue(args.force)
        self.assertTrue(args.offline)
        self.assertEqual(args.timeout, 15.0)

    def test_tgws_args(self) -> None:
        args = cli._tgws_install_args(version="v1.11.4", no_start=True)
        self.assertEqual(args.version, "v1.11.4")
        self.assertTrue(args.no_start)
        self.assertEqual(args.arch, "amd64")


if __name__ == "__main__":
    unittest.main()
