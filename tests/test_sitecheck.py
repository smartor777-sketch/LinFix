"""Тесты sitecheck: классификация причин недоступности сайта."""

from __future__ import annotations

import unittest
from unittest import mock

from linfix import sitecheck


class ResolveTests(unittest.TestCase):
    def test_bad_host_returns_empty(self) -> None:
        result = sitecheck.check_site("")
        self.assertEqual(result.status, "error")

    def test_dns_failure(self) -> None:
        with mock.patch.object(sitecheck, "_resolve", return_value=(None, "DNS: nxdomain")):
            result = sitecheck.check_site("nonexistent.invalid")
        self.assertEqual(result.status, "dns")
        self.assertIn("DNS", result.details[0])

    def test_strips_scheme_and_path(self) -> None:
        captured: dict[str, str] = {}

        def fake_resolve(host: str, timeout: float):
            captured["host"] = host
            return None, "DNS"

        with mock.patch.object(sitecheck, "_resolve", side_effect=fake_resolve):
            sitecheck.check_site("https://Example.COM/some/path?x=1")
        self.assertEqual(captured["host"], "example.com")


class ClassificationTests(unittest.TestCase):
    def _run(self, **patches) -> sitecheck.SiteCheck:
        defaults = {
            "_resolve": (mock.DEFAULT, (("1.2.3.4"), "DNS за 1 мс")),
        }
        with mock.patch.object(sitecheck, "_resolve", return_value=("1.2.3.4", "DNS за 1 мс")), \
             mock.patch.object(sitecheck, "_tcp_ok", **patches.get("tcp", {"return_value": (True, 0.05, "")})), \
             mock.patch.object(sitecheck, "_tls_handshake", **patches.get("tls", {"return_value": (True, 0.1, "TLSv1.3")})), \
             mock.patch.object(sitecheck, "_https_fetch", **patches.get("fetch", {"return_value": (20000, "ok", True)})), \
             mock.patch.object(sitecheck, "_http_ok", **patches.get("http", {"return_value": (False, 0, 0.0)})), \
             mock.patch.object(sitecheck, "_path_mtu", **patches.get("mtu", {"return_value": 1500})), \
             mock.patch.object(sitecheck, "_interface_mtu", **patches.get("iface_mtu", {"return_value": 1500})), \
             mock.patch.object(sitecheck, "_in_zapret_hostlist", **patches.get("in_list", {"return_value": False})):
            return sitecheck.check_site("example.com", timeout=3)

    def test_ok(self) -> None:
        result = self._run()
        self.assertEqual(result.status, "ok")

    def test_ip_block(self) -> None:
        result = self._run(
            tcp={"return_value": (False, 8.0, "timeout")},
            http={"return_value": (False, 0, 0.0)},
        )
        self.assertEqual(result.status, "ip_block")

    def test_http_only(self) -> None:
        result = self._run(
            tls={"return_value": (False, 8.0, "timeout")},
            http={"return_value": (True, 200, 0.3)},
        )
        self.assertEqual(result.status, "http_only")
        self.assertIn("HTTP", result.summary)

    def test_tls_ip_block_when_foreign_sni_fails(self) -> None:
        result = self._run(
            tls={"side_effect": [(False, 8.0, "timeout"), (False, 8.0, "timeout")]},
            http={"return_value": (False, 0, 0.0)},
        )
        self.assertEqual(result.status, "tls_ip")

    def test_sni_block_when_foreign_sni_works(self) -> None:
        result = self._run(
            tls={"side_effect": [(False, 8.0, "timeout"), (True, 0.2, "TLSv1.3")]},
        )
        self.assertEqual(result.status, "sni")
        self.assertIn("hostlist", result.advice)

    def test_pmtu_blackhole(self) -> None:
        # Локальный MTU 1500, а до сервера проходит 1492 — «дыра».
        result = self._run(
            fetch={"return_value": (8000, "таймаут", False)},
            mtu={"return_value": 1492},
            iface_mtu={"return_value": 1500},
        )
        self.assertEqual(result.status, "pmtu")
        self.assertIn("1492", result.summary)

    def test_pmtu_aligned_is_not_the_cause(self) -> None:
        # MTU уже согласован: 1492 == 1492, значит причина не в MTU.
        result = self._run(
            fetch={"return_value": (8000, "таймаут", False)},
            mtu={"return_value": 1492},
            iface_mtu={"return_value": 1492},
        )
        self.assertEqual(result.status, "slow")

    def test_slow_without_mtu_issue(self) -> None:
        result = self._run(
            fetch={"return_value": (8000, "таймаут", False)},
            mtu={"return_value": 1500},
            iface_mtu={"return_value": 1500},
        )
        self.assertEqual(result.status, "slow")

    def test_desync_blamed_when_domain_in_list(self) -> None:
        result = self._run(
            fetch={"return_value": (8000, "таймаут", False)},
            mtu={"return_value": 1500},
            iface_mtu={"return_value": 1500},
            in_list={"return_value": True},
        )
        self.assertEqual(result.status, "desync")
        self.assertIn("hostlist", result.advice)


if __name__ == "__main__":
    unittest.main()
