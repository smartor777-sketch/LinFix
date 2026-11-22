from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from linfix import api, tgws
from linfix.config import Config


class UserRotationTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.home = Path(self._tmp.name)
        self.config = self.home / ".config" / "TgWsProxy" / "config.json"
        self.config.parent.mkdir(parents=True)
        self.config.write_text(
            json.dumps(
                {
                    "cfproxy_user_domain": ["pclead.co.uk", "offshor.co.uk"],
                    "cfproxy_user_domain_enabled": True,
                }
            ),
            encoding="utf-8",
        )
        self._home_patch = mock.patch.object(Path, "home", return_value=self.home)
        self._home_patch.start()
        self.addCleanup(self._home_patch.stop)

    def test_add_domain_to_user_rotation(self) -> None:
        with mock.patch.object(tgws, "install_user_unit", return_value=(self.config, False)), \
             mock.patch.object(api.util, "run", return_value=mock.Mock(returncode=0)):
            code, payload = api.update_user_rotation_domains(
                Config(), add=["cakeisalie.co.uk"]
            )
        self.assertEqual(code, api.EXIT_OK)
        self.assertTrue(payload["changed"])
        self.assertIn("cakeisalie.co.uk", payload["domains"])
        data = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertIn("cakeisalie.co.uk", data["cfproxy_user_domain"])

    def test_remove_domain_from_user_rotation(self) -> None:
        with mock.patch.object(tgws, "install_user_unit", return_value=(self.config, False)), \
             mock.patch.object(api.util, "run", return_value=mock.Mock(returncode=0)):
            code, payload = api.update_user_rotation_domains(
                Config(), remove=["offshor.co.uk"]
            )
        self.assertEqual(code, api.EXIT_OK)
        self.assertEqual(payload["domains"], ["pclead.co.uk"])

    def test_cannot_remove_last_domain(self) -> None:
        code, payload = api.update_user_rotation_domains(
            Config(), remove=["pclead.co.uk", "offshor.co.uk"]
        )
        self.assertEqual(code, api.EXIT_USAGE)
        self.assertFalse(payload["ok"])

    def test_saved_only_in_github_mode(self) -> None:
        self.config.write_text(
            json.dumps({"cfproxy_user_domain": [], "cfproxy_user_domain_enabled": False}),
            encoding="utf-8",
        )
        code, payload = api.update_user_rotation_domains(
            Config(), add=["sadnews.co.uk"]
        )
        self.assertEqual(code, api.EXIT_OK)
        self.assertTrue(payload["saved_only"])
        data = json.loads(self.config.read_text(encoding="utf-8"))
        self.assertEqual(data["cfproxy_user_domain"], [])


if __name__ == "__main__":
    unittest.main()
