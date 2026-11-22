from __future__ import annotations

import unittest
from unittest import mock

from linfix import sitecheck


class InHostlistTests(unittest.TestCase):
    def test_finds_exact_domain(self) -> None:
        with mock.patch("pathlib.Path.read_text", return_value="rutracker.org\n"):
            result = sitecheck._in_zapret_hostlist("rutracker.org")
        self.assertTrue(result)

    def test_finds_subdomain(self) -> None:
        with mock.patch("pathlib.Path.read_text", return_value="rutracker.org\n"):
            result = sitecheck._in_zapret_hostlist("www.rutracker.org")
        self.assertTrue(result)

    def test_ignores_comments(self) -> None:
        with mock.patch("pathlib.Path.read_text", return_value="# rutracker.org\n"):
            result = sitecheck._in_zapret_hostlist("rutracker.org")
        self.assertFalse(result)


class SniStatusTests(unittest.TestCase):
    """Домен в hostlist + SNI-блокировка => статус desync, а не sni."""

    def _check(self, in_list: bool) -> sitecheck.SiteCheck:
        with mock.patch.object(sitecheck, "_resolve", return_value=("1.1.1.1", "")), \
             mock.patch.object(sitecheck, "_tcp_ok", return_value=(True, 0.05, "")), \
             mock.patch.object(
                 sitecheck, "_tls_handshake",
                 side_effect=[(False, 0.0, "timeout"), (True, 0.1, "TLSv1.3")],
             ), \
             mock.patch.object(sitecheck, "_in_zapret_hostlist", return_value=in_list):
            return sitecheck.check_site("rutracker.org")

    def test_domain_in_hostlist_gives_desync(self) -> None:
        result = self._check(True)
        self.assertEqual(result.status, "desync")
        self.assertIn("добавлять повторно не нужно", result.advice)

    def test_domain_not_in_hostlist_gives_sni(self) -> None:
        result = self._check(False)
        self.assertEqual(result.status, "sni")
        self.assertIn("Добавьте", result.advice)


if __name__ == "__main__":
    unittest.main()
