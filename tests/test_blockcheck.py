"""Тесты linfix.blockcheck: маркер прогона и остановка группы процессов."""

from __future__ import annotations

import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from linfix import blockcheck


class LockTests(unittest.TestCase):
    def test_running_detects_own_pid(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "blockcheck.lock"
            blockcheck.write_lock(lock, domains=["a.org"])
            data = blockcheck.running(lock)
            self.assertEqual(data.get("pid"), os.getpid())
            self.assertEqual(data.get("domains"), ["a.org"])
            blockcheck.clear_lock(lock)
            self.assertEqual(blockcheck.running(lock), {})

    def test_dead_pid_is_not_running(self) -> None:
        proc = subprocess.Popen(["true"])
        proc.wait()
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "blockcheck.lock"
            lock.write_text(json.dumps({"pid": proc.pid}), encoding="utf-8")
            self.assertEqual(blockcheck.running(lock), {})

    def test_garbage_lock_is_ignored(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            lock = Path(tmp) / "blockcheck.lock"
            lock.write_text("не json", encoding="utf-8")
            self.assertEqual(blockcheck.read_lock(lock), {})
            self.assertEqual(blockcheck.running(lock), {})

    def test_pid_alive(self) -> None:
        self.assertTrue(blockcheck.pid_alive(os.getpid()))
        self.assertFalse(blockcheck.pid_alive(0))
        proc = subprocess.Popen(["true"])
        proc.wait()
        self.assertFalse(blockcheck.pid_alive(proc.pid))


class TimeoutTests(unittest.TestCase):
    def test_check_readiness_reports_running_scan(self) -> None:
        from unittest.mock import patch

        from linfix.config import Config

        with patch.object(blockcheck, "running", return_value={"pid": 123}):
            ok, reason = blockcheck.check_readiness(Config())
        self.assertFalse(ok)
        self.assertIn("уже идёт", reason)

    def test_timeout_stops_whole_group(self) -> None:
        """Таймаут должен гасить и сам скрипт, и его дочерние процессы."""

        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            child_pid_file = base / "child.pid"
            script = base / "blockcheck2.sh"
            script.write_text(
                f'sleep 60 &\necho $! > "{child_pid_file}"\nwait\n',
                encoding="utf-8",
            )

            started = time.monotonic()
            result = blockcheck.run_blockcheck(
                base, domains=["a.org"], level="quick", timeout=1.0
            )
            elapsed = time.monotonic() - started

            self.assertIn("таймаут", result.error)
            # Не ждём полную минуту сна и не висим на пайпах.
            self.assertLess(elapsed, 20.0)

            self.assertTrue(child_pid_file.exists())
            child = int(child_pid_file.read_text(encoding="utf-8").strip())
            deadline = time.monotonic() + 5.0
            while blockcheck.pid_alive(child) and time.monotonic() < deadline:
                time.sleep(0.05)
            self.assertFalse(blockcheck.pid_alive(child), "дочерний sleep пережил таймаут")


if __name__ == "__main__":
    unittest.main()


class LastResultTests(unittest.TestCase):
    def test_result_from_payload_is_session_only(self) -> None:
        result = blockcheck.result_from_payload(
            {
                "domains": ["kws2.pclead.co.uk"],
                "level": "quick",
                "strategies": [{"success": 1, "total": 2, "args": "--lua-desync=fake"}],
                "error": "таймаут",
                "works_without_bypass": False,
            }
        )
        self.assertEqual(result.domains, ["kws2.pclead.co.uk"])
        self.assertEqual(result.level, "quick")
        self.assertEqual(result.error, "таймаут")
        self.assertEqual(len(result.strategies), 1)
        self.assertEqual(result.best.args, "--lua-desync=fake")

    def test_save_read_roundtrip(self) -> None:
        from pathlib import Path

        import tempfile

        result = blockcheck.BlockcheckResult(
            domains=["a.org"],
            level="quick",
            strategies=[blockcheck.Strategy(2, 2, "--filter-tcp=443 --lua-desync=fake")],
            error="",
        )
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "last.json"
            blockcheck.save_last_result(result, path)
            data = blockcheck.read_last_result(path)
        self.assertIsNotNone(data)
        assert data is not None
        self.assertEqual(data.level, "quick")
        self.assertEqual(len(data.strategies), 1)
        self.assertEqual(data.strategies[0].args, "--filter-tcp=443 --lua-desync=fake")
        self.assertEqual(data.strategies[0].score, 1.0)
        self.assertEqual(data.best, data.strategies[0])

    def test_read_missing(self) -> None:
        from pathlib import Path

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            self.assertIsNone(blockcheck.read_last_result(Path(tmp) / "nope.json"))

    def test_read_garbage(self) -> None:
        from pathlib import Path

        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "garbage.json"
            p.write_text("не json", encoding="utf-8")
            self.assertIsNone(blockcheck.read_last_result(p))


class StreamingTests(unittest.TestCase):
    """Потоковый сбор: кандидаты берутся из AVAILABLE, не дожидаясь SUMMARY."""

    def _script(self, body: str) -> Path:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        base = Path(tmp.name)
        script = base / "blockcheck2.sh"
        script.write_text(body, encoding="utf-8")
        return base

    def test_collects_from_stream(self) -> None:
        base = self._script(
            'echo "- curl_test_https_tls12 ipv4 a.org : nfqws2 --lua-desync=fake:blob=x"\n'
            'echo "!!!!! AVAILABLE !!!!!"\n'
            'echo "- curl_test_https_tls12 ipv4 a.org : nfqws2 --lua-desync=multisplit:pos=1"\n'
            'echo "UNAVAILABLE code=28"\n'
            'echo "- curl_test_https_tls12 ipv4 a.org : nfqws2 --lua-desync=multidisorder:pos=2"\n'
            'echo "!!!!! AVAILABLE !!!!!"\n'
            "sleep 5\n"
        )
        result = blockcheck.run_blockcheck(
            base, domains=["a.org"], level="quick", timeout=3.0
        )
        args = [s.args for s in result.strategies]
        self.assertEqual(len(args), 2)
        self.assertTrue(any("fake:blob=x" in a for a in args))
        self.assertTrue(any("multidisorder:pos=2" in a for a in args))
        self.assertFalse(any("multisplit" in a for a in args))
        self.assertTrue(result.streamed)

    def test_timeout_keeps_partial(self) -> None:
        """При таймауте найденное не теряется — раньше терялось всё."""

        base = self._script(
            'echo "- curl_test_https_tls12 ipv4 a.org : nfqws2 --lua-desync=fake:blob=x"\n'
            'echo "!!!!! AVAILABLE !!!!!"\n'
            "sleep 30\n"
        )
        started = time.monotonic()
        result = blockcheck.run_blockcheck(
            base, domains=["a.org"], level="quick", timeout=2.0
        )
        self.assertLess(time.monotonic() - started, 15)
        self.assertEqual(len(result.strategies), 1)
        self.assertIn("таймаут", result.error)
        self.assertTrue(result.streamed)

    def test_attempt_lines_do_not_capture(self) -> None:
        """«[attempt 1] AVAILABLE» — это повтор, а не новый кандидат."""

        base = self._script(
            'echo "- curl_test_https_tls12 ipv4 a.org : nfqws2 --lua-desync=fake:blob=x"\n'
            'echo "[attempt 1] AVAILABLE"\n'
            'echo "[attempt 2] AVAILABLE"\n'
            'echo "!!!!! AVAILABLE !!!!!"\n'
            "sleep 5\n"
        )
        result = blockcheck.run_blockcheck(
            base, domains=["a.org"], level="quick", timeout=3.0
        )
        self.assertEqual(len(result.strategies), 1)

    def test_summary_still_parsed(self) -> None:
        """Если SUMMARY успел напечататься, он важнее потоковых догадок."""

        base = self._script(
            'echo "2/2 : --filter-tcp=443 --lua-desync=fake:blob=x"\n'
        )
        result = blockcheck.run_blockcheck(
            base, domains=["a.org"], level="quick", timeout=5.0
        )
        self.assertEqual(len(result.strategies), 1)
        self.assertEqual(result.strategies[0].success, 2)
        self.assertFalse(result.streamed)
