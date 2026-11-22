from __future__ import annotations

import unittest
from unittest import mock

from linfix import cfprofile


def probe(domain: str, *, tls=True, body=True, error=""):
    return cfprofile.DomainProbe(
        domain=domain,
        host=f"kws2.{domain}",
        tls_ok=tls,
        tls_ms=100,
        h2_status=404 if body else None,
        h2_ms=400 if body else 8000,
        h2_error=error,
        body_ok=body,
    )


class CheckProfileTests(unittest.TestCase):
    def test_body_ok_when_all_probes_pass(self) -> None:
        with mock.patch.object(
            cfprofile, "probe_domain", side_effect=lambda d, **_k: probe(d)
        ):
            report = cfprofile.check_profile(["pclead.co.uk", "offshor.co.uk"])
        self.assertTrue(report.tls_ok)
        self.assertTrue(report.body_ok)
        self.assertIn("медиа должно грузиться", report.detection)
        self.assertEqual(report.hint, "")

    def test_detects_tcpseg_like_profile(self) -> None:
        with mock.patch.object(
            cfprofile,
            "probe_domain",
            side_effect=lambda d, **_k: probe(d, body=False, error="ReadTimeout"),
        ):
            report = cfprofile.check_profile(["pclead.co.uk"])
        self.assertTrue(report.tls_ok)
        self.assertFalse(report.body_ok)
        self.assertIn("tcpseg", report.detection)
        self.assertIn("apply-cf-profile", report.hint)

    def test_detects_broken_tls(self) -> None:
        with mock.patch.object(
            cfprofile,
            "probe_domain",
            side_effect=lambda d, **_k: probe(d, tls=False, body=False, error="SSLError"),
        ):
            report = cfprofile.check_profile(["pclead.co.uk"])
        self.assertFalse(report.tls_ok)
        self.assertIn("TLS не проходит", report.detection)

    def test_empty_domain_list(self) -> None:
        report = cfprofile.check_profile([])
        self.assertFalse(report.body_ok)
        self.assertEqual(report.probes, ())
        self.assertIn("нет доменов", report.detection)

    def test_limit_applies(self) -> None:
        calls: list[str] = []

        def fake(domain, **_kwargs):
            calls.append(domain)
            return probe(domain)

        with mock.patch.object(cfprofile, "probe_domain", side_effect=fake):
            report = cfprofile.check_profile(
                ["a.co.uk", "b.co.uk", "c.co.uk", "d.co.uk"], limit=2
            )
        self.assertEqual(calls, ["a.co.uk", "b.co.uk"])
        self.assertEqual(len(report.probes), 2)

    def test_to_dict_shape(self) -> None:
        report = cfprofile.ProfileReport(
            probes=(probe("x.co.uk"),),
            tls_ok=True,
            body_ok=True,
            detection="ok",
            hint="",
        )
        payload = report.to_dict()
        self.assertEqual(payload["body_bytes"], cfprofile.BODY_BYTES)
        self.assertEqual(payload["probes"][0]["domain"], "x.co.uk")


if __name__ == "__main__":
    unittest.main()
