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


class ParallelProbeTests(unittest.TestCase):
    """Домены проверяются параллельно, но порядок результатов сохраняется."""

    def test_order_preserved(self) -> None:
        domains = ["a.co.uk", "b.co.uk", "c.co.uk", "d.co.uk"]

        def fake_probe(domain, **_kwargs):
            # Разная задержка: при последовательном обходе порядок тоже был бы
            # верным, поэтому проверяем именно сохранение порядка.
            return probe(domain)

        with mock.patch.object(cfprofile, "probe_domain", side_effect=fake_probe):
            probes = cfprofile._probe_many(domains)
        self.assertEqual([p.domain for p in probes], domains)

    def test_parallel_is_faster(self) -> None:
        import threading
        import time

        active = {"now": 0, "peak": 0}
        lock = threading.Lock()

        def fake_probe(domain, **_kwargs):
            with lock:
                active["now"] += 1
                active["peak"] = max(active["peak"], active["now"])
            time.sleep(0.12)
            with lock:
                active["now"] -= 1
            return probe(domain)

        started = time.monotonic()
        with mock.patch.object(cfprofile, "probe_domain", side_effect=fake_probe):
            probes = cfprofile._probe_many(["a", "b", "c", "d"])
        elapsed = time.monotonic() - started

        self.assertEqual(len(probes), 4)
        # При последовательном обходе было бы ~0.48 с.
        self.assertLess(elapsed, 0.35)
        self.assertGreater(active["peak"], 1, "проверки не шли параллельно")

    def test_single_domain_no_pool(self) -> None:
        calls: list[str] = []

        def fake_probe(domain, **_kwargs):
            calls.append(domain)
            return probe(domain)

        with mock.patch.object(cfprofile, "probe_domain", side_effect=fake_probe):
            probes = cfprofile._probe_many(["only.co.uk"])
        self.assertEqual(len(probes), 1)
        self.assertEqual(calls, ["only.co.uk"])

    def test_empty_list(self) -> None:
        self.assertEqual(cfprofile._probe_many([]), ())

    def test_check_profile_uses_parallel_helper(self) -> None:
        domains = ["a.co.uk", "b.co.uk", "c.co.uk"]
        with mock.patch.object(
            cfprofile, "_probe_many", return_value=tuple(probe(d) for d in domains)
        ) as helper:
            cfprofile.check_profile(domains)
        helper.assert_called_once()


class ProbeHostsParallelTests(unittest.TestCase):
    """Отбор кандидатов: проверка набора хостов параллельно."""

    def test_returns_map(self) -> None:
        def fake_probe(host, timeout):
            return (404, 100, "")

        with mock.patch.object(cfprofile, "_h2_probe", side_effect=fake_probe):
            result = cfprofile.probe_hosts_parallel(
                ["kws2.a.co.uk", "kws2.b.co.uk"], timeout=5
            )
        self.assertEqual(set(result), {"kws2.a.co.uk", "kws2.b.co.uk"})
        self.assertTrue(all(ok for ok, _ms in result.values()))

    def test_failure_recorded(self) -> None:
        def fake_probe(host, timeout):
            if "bad" in host:
                return (None, 8000, "ReadTimeout")
            return (404, 100, "")

        with mock.patch.object(cfprofile, "_h2_probe", side_effect=fake_probe):
            result = cfprofile.probe_hosts_parallel(["kws2.good.co.uk", "kws2.bad.co.uk"])
        self.assertTrue(result["kws2.good.co.uk"][0])
        self.assertFalse(result["kws2.bad.co.uk"][0])

    def test_empty(self) -> None:
        self.assertEqual(cfprofile.probe_hosts_parallel([]), {})

    def test_parallel(self) -> None:
        import threading
        import time

        active = {"now": 0, "peak": 0}
        lock = threading.Lock()

        def fake_probe(host, timeout):
            with lock:
                active["now"] += 1
                active["peak"] = max(active["peak"], active["now"])
            time.sleep(0.1)
            with lock:
                active["now"] -= 1
            return (404, 100, "")

        started = time.monotonic()
        with mock.patch.object(cfprofile, "_h2_probe", side_effect=fake_probe):
            cfprofile.probe_hosts_parallel([f"kws2.h{i}.co.uk" for i in range(4)])
        self.assertLess(time.monotonic() - started, 0.3)
        self.assertGreater(active["peak"], 1)
