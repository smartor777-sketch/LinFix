from __future__ import annotations

import unittest

from linfix import guard


class GuardTests(unittest.TestCase):
    def test_count(self) -> None:
        lines = [
            "2026-11-20 CF proxy failed for DC2",
            "cf proxy FAILED for DC4",
            "WS pool unavailable",
            "no fallback",
        ]
        self.assertEqual(guard.count_cf_failures(lines), 2)

    def test_trip_only_when_tls_broken(self) -> None:
        self.assertTrue(guard.should_trip(20, 20, tls_ok=False))
        self.assertFalse(guard.should_trip(20, 20, tls_ok=True))
        self.assertFalse(guard.should_trip(19, 20, tls_ok=False))
        self.assertFalse(guard.should_trip(100, 0, tls_ok=False))

    def test_evaluate_disabled(self) -> None:
        state = guard.evaluate(
            ["CF proxy failed"] * 30,
            threshold=20,
            tls_ok=False,
            enabled=False,
        )
        self.assertEqual(state.failures, 30)
        self.assertFalse(state.tripped)

    def test_evaluate_trips(self) -> None:
        state = guard.evaluate(
            ["CF proxy failed"] * 21,
            threshold=20,
            tls_ok=False,
            window_seconds=60,
        )
        self.assertTrue(state.tripped)
        self.assertEqual(state.window_seconds, 60)


if __name__ == "__main__":
    unittest.main()
