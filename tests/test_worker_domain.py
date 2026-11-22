from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from linfix import tgws


class WorkerDomainConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.path = Path(self._tmp.name) / "config.json"
        self.path.write_text(json.dumps({"h2": True}), encoding="utf-8")

    def test_worker_domain_and_flag(self) -> None:
        data, backup, changed = tgws.update_config(
            self.path,
            {
                "cfproxy_worker_domain": ["tgws.abcd.workers.dev"],
                "cfproxy_worker_enabled": True,
            },
        )
        self.assertTrue(changed)
        self.assertTrue(backup is not None)
        self.assertEqual(data["cfproxy_worker_domain"], ["tgws.abcd.workers.dev"])
        self.assertIs(data["cfproxy_worker_enabled"], True)

    def test_invalid_worker_domain(self) -> None:
        with self.assertRaises(tgws.TgwsError):
            tgws.update_config(self.path, {"cfproxy_worker_domain": ["not a domain"]})

    def test_worker_flag_must_be_bool(self) -> None:
        with self.assertRaises(tgws.TgwsError):
            tgws.update_config(self.path, {"cfproxy_worker_enabled": "yes"})

    def test_cli_args_include_worker_domain(self) -> None:
        args = tgws.cli_args(
            {
                "port": 1080,
                "host": "127.0.0.1",
                "cfproxy_worker_domain": ["tgws.abcd.workers.dev"],
            }
        )
        joined = " ".join(args)
        self.assertIn("--cfproxy-worker-domain tgws.abcd.workers.dev", joined)


if __name__ == "__main__":
    unittest.main()
