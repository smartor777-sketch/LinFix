from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from linfix import helper, state

DOMAINS = ["pclead.co.uk", "offshor.co.uk", "cakeisalie.co.uk"]


class ApplyDomainsTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.hostlist = self.root / "zapret-hosts-user.txt"
        self.state_path = self.root / "state.json"
        self.hostlist.write_text("example.com\n", encoding="utf-8")

    def apply(self, *, restart_on_change=True, restart_func=None, sha=""):
        return helper.apply_domains(
            self.hostlist,
            DOMAINS,
            marker="# linfix: tgws cf domains",
            service="zapret2",
            restart_on_change=restart_on_change,
            state_path=self.state_path,
            source_label="github",
            source_url="https://example.com/list.txt",
            source_sha256=sha,
            restart_func=restart_func,
        )

    def test_apply_success_and_state(self) -> None:
        calls: list[str] = []

        def runner(service: str):
            calls.append(service)
            return True, "active=true", ""

        code, payload = self.apply(restart_func=runner)

        self.assertEqual(code, 0)
        self.assertTrue(payload["ok"])
        self.assertTrue(payload["changed"])
        self.assertTrue(payload["restarted"])
        self.assertEqual(calls, ["zapret2"])
        self.assertIsNotNone(payload["backup"])
        self.assertTrue(Path(str(payload["backup"])).exists())

        text = self.hostlist.read_text(encoding="utf-8")
        self.assertIn("example.com", text)
        for domain in DOMAINS:
            self.assertIn(domain, text)

        saved = state.load_state(self.state_path)
        self.assertEqual(saved.domains, tuple(DOMAINS))
        self.assertEqual(saved.domains_count, len(DOMAINS))
        self.assertEqual(saved.service, "zapret2")
        self.assertIsNone(saved.last_error)

    def test_second_apply_does_not_restart(self) -> None:
        self.apply(restart_func=lambda service: (True, "ok", ""))

        def forbidden(service: str):
            raise AssertionError("рестарт не должен вызываться без изменений")

        code, payload = self.apply(restart_func=forbidden)
        self.assertEqual(code, 0)
        self.assertFalse(payload["changed"])
        self.assertFalse(payload["restarted"])
        self.assertIsNone(payload["backup"])

    def test_no_restart_when_disabled(self) -> None:
        def forbidden(service: str):
            raise AssertionError("рестарт выключен в конфиге")

        code, payload = self.apply(restart_on_change=False, restart_func=forbidden)
        self.assertEqual(code, 0)
        self.assertTrue(payload["changed"])
        self.assertFalse(payload["restarted"])

    def test_rollback_on_restart_failure(self) -> None:
        calls: list[str] = []

        def runner(service: str):
            calls.append(service)
            if len(calls) == 1:
                return False, "active=false", "journal: service failed"
            return True, "active=true", ""

        code, payload = self.apply(restart_func=runner)

        self.assertEqual(code, 5)
        self.assertFalse(payload["ok"])
        self.assertTrue(payload["rollback"])
        self.assertEqual(calls, ["zapret2", "zapret2"])
        self.assertEqual(self.hostlist.read_text(encoding="utf-8"), "example.com\n")

        saved = state.load_state(self.state_path)
        self.assertIn("active=false", saved.last_error or "")
        self.assertIn("journal: service failed", saved.last_error or "")

    def test_sha_mismatch_rejected_before_changes(self) -> None:
        with self.assertRaises(helper.HelperError):
            self.apply(sha="0" * 64)
        self.assertEqual(self.hostlist.read_text(encoding="utf-8"), "example.com\n")
        self.assertFalse(self.state_path.exists())

    def test_correct_sha_accepted(self) -> None:
        code, payload = self.apply(
            sha=state.domains_digest(DOMAINS),
            restart_func=lambda service: (True, "ok", ""),
        )
        self.assertEqual(code, 0)
        self.assertTrue(payload["changed"])


class ExtraValidationTests(unittest.TestCase):
    def test_sha256(self) -> None:
        self.assertEqual(helper.validate_sha256("A" * 64), "a" * 64)
        self.assertEqual(helper.validate_sha256(""), "")
        with self.assertRaises(helper.HelperError):
            helper.validate_sha256("abc")

    def test_source_url(self) -> None:
        self.assertEqual(
            helper.validate_source_url("https://example.com/x"),
            "https://example.com/x",
        )
        with self.assertRaises(helper.HelperError):
            helper.validate_source_url("http://example.com/x")

    def test_domains_apply_is_whitelisted(self) -> None:
        self.assertEqual(helper.validate_action("domains.apply"), "domains.apply")


if __name__ == "__main__":
    unittest.main()
