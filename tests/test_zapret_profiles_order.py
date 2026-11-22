from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from linfix import zapret


class ActiveProfileOrderTests(unittest.TestCase):
    """Какой профиль реально применится к домену с учётом порядка."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.cf_list = self.root / "linfix-cf-hosts.txt"
        self.cf_list.write_text("kws2.pclead.co.uk\nkws203.cakeisalie.co.uk\n", encoding="utf-8")
        self.rut_list = self.root / "zapret-hosts-rut.txt"
        self.rut_list.write_text("rutracker.org\n", encoding="utf-8")
        self.profiles = [
            {
                "active": True,
                "hostlists": [self.cf_list.name],
                "hostlist_paths": [str(self.cf_list)],
                "bare_hostlists": [],
                "bare_hostlist_paths": [],
                "uses_placeholder": False,
                "desync": ["fake:blob=fake_default_tls:tcp_md5"],
            },
            {
                "active": True,
                "hostlists": [],
                "hostlist_paths": [],
                "bare_hostlists": [self.rut_list.name],
                "bare_hostlist_paths": [str(self.rut_list)],
                "uses_placeholder": False,
                "desync": ["tcpseg:pos=0,1"],
            },
            {
                "active": True,
                "hostlists": [],
                "hostlist_paths": [],
                "bare_hostlists": [],
                "bare_hostlist_paths": [],
                "uses_placeholder": True,
                "desync": ["multisplit:pos=1"],
            },
        ]

    def test_cf_domain_uses_cf_profile(self) -> None:
        profile = zapret.active_profile_for(self.profiles, "kws2.pclead.co.uk")
        self.assertEqual(profile["desync"], ["fake:blob=fake_default_tls:tcp_md5"])

    def test_other_domain_falls_to_bare_profile(self) -> None:
        profile = zapret.active_profile_for(self.profiles, "example.com")
        self.assertEqual(profile["desync"], ["tcpseg:pos=0,1"])

    def test_domain_in_bare_list_still_global(self) -> None:
        profile = zapret.active_profile_for(self.profiles, "rutracker.org")
        self.assertEqual(profile["desync"], ["tcpseg:pos=0,1"])

    def test_bare_profile_first_still_global(self) -> None:
        swapped = [self.profiles[1], self.profiles[0], self.profiles[2]]
        profile = zapret.active_profile_for(swapped, "kws2.pclead.co.uk")
        self.assertEqual(profile["desync"], ["tcpseg:pos=0,1"])

    def test_missing_hostlist_file_does_not_break(self) -> None:
        self.cf_list.unlink()
        profile = zapret.active_profile_for(self.profiles, "kws2.pclead.co.uk")
        self.assertEqual(profile["desync"], ["tcpseg:pos=0,1"])

    def test_placeholder_is_last_resort(self) -> None:
        profiles = [self.profiles[2]]
        profile = zapret.active_profile_for(profiles, "example.com")
        self.assertEqual(profile["desync"], ["multisplit:pos=1"])


if __name__ == "__main__":
    unittest.main()
