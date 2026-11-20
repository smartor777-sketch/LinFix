from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from netfix import state

DOMAINS = ["pclead.co.uk", "offshor.co.uk"]


class DigestTests(unittest.TestCase):
    def test_order_independent(self) -> None:
        self.assertEqual(
            state.domains_digest(["offshor.co.uk", "pclead.co.uk"]),
            state.domains_digest(DOMAINS),
        )

    def test_case_and_trailing_dot_ignored(self) -> None:
        self.assertEqual(
            state.domains_digest(["PCLEAD.CO.UK."]),
            state.domains_digest(["pclead.co.uk"]),
        )

    def test_different_lists_differ(self) -> None:
        self.assertNotEqual(state.domains_digest(DOMAINS), state.domains_digest(["pclead.co.uk"]))


class StateTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_missing_file(self) -> None:
        loaded = state.load_state(self.root / "nope.json")
        self.assertEqual(loaded.domains, ())
        self.assertIsNone(loaded.domains_updated_at)

    def test_corrupt_file(self) -> None:
        path = self.root / "state.json"
        path.write_text("not json", encoding="utf-8")
        loaded = state.load_state(path)
        self.assertIn("не удалось прочитать", loaded.last_error or "")

    def test_roundtrip(self) -> None:
        original = state.State().record_domains(
            DOMAINS,
            source_label="github",
            source_url="https://example.com/list.txt",
            hostlist="/opt/zapret2/ipset/zapret-hosts-user.txt",
            service="zapret2",
        )
        path = original.save(self.root / "state.json")
        loaded = state.load_state(path)

        self.assertEqual(loaded.domains, tuple(DOMAINS))
        self.assertEqual(loaded.domains_count, 2)
        self.assertEqual(loaded.domains_sha256, state.domains_digest(DOMAINS))
        self.assertEqual(loaded.domains_source, "github")
        self.assertEqual(loaded.hostlist, "/opt/zapret2/ipset/zapret-hosts-user.txt")
        self.assertEqual(loaded.service, "zapret2")
        self.assertIsNone(loaded.last_error)
        self.assertIsNotNone(loaded.domains_updated_at)

    def test_record_tgws(self) -> None:
        updated = state.State().record_tgws(
            "v1.11.4", "a" * 64, "/opt/tgwsproxy/TgWsProxy"
        )
        path = updated.save(self.root / "state.json")
        loaded = state.load_state(path)
        self.assertEqual(loaded.tgws_version, "v1.11.4")
        self.assertEqual(loaded.tgws_sha256, "a" * 64)
        self.assertEqual(loaded.tgws_binary, "/opt/tgwsproxy/TgWsProxy")
        self.assertIsNotNone(loaded.tgws_installed_at)

    def test_record_error(self) -> None:
        updated = state.State().record_error("restart failed\njournal tail")
        self.assertIn("restart failed", updated.last_error or "")
        self.assertIsNotNone(updated.updated_at)

    def test_version_and_unknown_fields(self) -> None:
        loaded = state.State.from_dict(
            {"version": 1, "domains": ["pclead.co.uk"], "unknown": 42}
        )
        self.assertEqual(loaded.domains, ("pclead.co.uk",))
        self.assertEqual(loaded.version, 1)


if __name__ == "__main__":
    unittest.main()
