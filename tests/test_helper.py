from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from netfix import helper


class PathValidationTests(unittest.TestCase):
    def test_allows_paths_under_zapret(self) -> None:
        path = helper.validate_path(
            "/opt/zapret2/ipset/zapret-hosts-user.txt", must_exist=False
        )
        self.assertEqual(str(path), "/opt/zapret2/ipset/zapret-hosts-user.txt")

    def test_rejects_outside_allowed_roots(self) -> None:
        with self.assertRaises(helper.HelperError):
            helper.validate_path("/etc/passwd")

    def test_rejects_relative_path(self) -> None:
        with self.assertRaises(helper.HelperError):
            helper.validate_path("opt/zapret2/ipset/hosts")

    def test_rejects_traversal(self) -> None:
        with self.assertRaises(helper.HelperError):
            helper.validate_path("/opt/zapret2/../../etc/passwd")

    def test_rejects_symlink_pointing_outside(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            link = Path(tmp) / "passwd"
            link.symlink_to("/etc/passwd")
            with self.assertRaises(helper.HelperError):
                helper.validate_path(str(link), must_exist=True)

    def test_rejects_gzip_list(self) -> None:
        with self.assertRaises(helper.HelperError):
            helper.validate_path("/opt/zapret2/ipset/zapret-hosts.txt.gz", must_exist=False)


class DomainValidationTests(unittest.TestCase):
    def test_valid_and_normalized(self) -> None:
        self.assertEqual(helper.validate_domain("  PCLEAD.CO.UK "), "pclead.co.uk")

    def test_invalid(self) -> None:
        for value in ("bad_domain", "http://x.com", "-bad.com", ""):
            with self.subTest(value=value):
                with self.assertRaises(helper.HelperError):
                    helper.validate_domain(value)


class ActionValidationTests(unittest.TestCase):
    def test_known_actions(self) -> None:
        self.assertEqual(helper.validate_action("domains.merge"), "domains.merge")
        self.assertEqual(helper.validate_action("zapret.restart"), "zapret.restart")

    def test_unknown_action(self) -> None:
        with self.assertRaises(helper.HelperError):
            helper.validate_action("shell.exec")


class ServiceValidationTests(unittest.TestCase):
    def test_known_services(self) -> None:
        self.assertEqual(helper.validate_service("zapret"), "zapret")
        self.assertEqual(helper.validate_service("zapret2"), "zapret2")

    def test_unknown_service(self) -> None:
        with self.assertRaises(helper.HelperError):
            helper.validate_service("ssh")


if __name__ == "__main__":
    unittest.main()
