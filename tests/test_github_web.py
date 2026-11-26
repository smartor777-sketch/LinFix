from __future__ import annotations

import unittest
from unittest import mock

from linfix import github_web, tgws, updates


class GitHubWebTests(unittest.TestCase):
    def test_latest_tag_from_redirect(self) -> None:
        response = mock.Mock()
        response.geturl.return_value = (
            "https://github.com/bol-van/zapret2/releases/tag/v1.0.8"
        )
        with mock.patch.object(
            github_web.urllib.request, "urlopen", return_value=response
        ) as urlopen:
            tag = github_web.latest_tag("bol-van/zapret2")

        self.assertEqual(tag, "v1.0.8")
        self.assertIn("/releases/latest", urlopen.call_args.args[0].full_url)

    def test_latest_tag_falls_back_to_atom(self) -> None:
        response = mock.Mock()
        response.geturl.return_value = "https://github.com/x/y/releases"
        atom = (
            '<feed><entry><link href="https://github.com/x/y/releases/tag/v2.0"/>'
            "</entry></feed>"
        )
        with mock.patch.object(github_web.urllib.request, "urlopen", return_value=response):
            with mock.patch.object(github_web, "_fetch_text", return_value=atom):
                tag = github_web.latest_tag("x/y")
        self.assertEqual(tag, "v2.0")

    def test_assets_parse_digest_and_size(self) -> None:
        page = """
        <a href="/bol-van/zapret2/releases/download/v1.0.8/zapret2-v1.0.8.tar.gz">
        </a>
        <span>sha256:fbd2c39941eea436dce5df4ab51fd19163f31d6b6b49131195400cc552fe77ff</span>
        <span>9.08 MB</span>
        <a href="/bol-van/zapret2/releases/download/v1.0.8/sha256sum.txt"></a>
        <span>sha256:46cc228fb915e3d09beeff719a37c5ce98a919f23889dee076a81806dabda627</span>
        <span>6.46 KB</span>
        """
        with mock.patch.object(github_web, "_fetch_text", return_value=page):
            assets = github_web.release_assets("bol-van/zapret2", "v1.0.8")

        self.assertEqual(len(assets), 2)
        first, second = assets
        self.assertEqual(first.name, "zapret2-v1.0.8.tar.gz")
        self.assertEqual(
            first.sha256,
            "fbd2c39941eea436dce5df4ab51fd19163f31d6b6b49131195400cc552fe77ff",
        )
        self.assertEqual(first.size, int(9.08 * 1024 * 1024))
        self.assertEqual(second.name, "sha256sum.txt")
        self.assertEqual(second.size, int(6.46 * 1024))

    def test_assets_require_files(self) -> None:
        with mock.patch.object(github_web, "_fetch_text", return_value="<html></html>"):
            with self.assertRaises(github_web.GitHubWebError):
                github_web.release_assets("bol-van/zapret2", "v1.0.8")

    def test_http_error(self) -> None:
        error = github_web.urllib.error.HTTPError(
            "https://github.com/x", 500, "boom", {}, None
        )
        with mock.patch.object(github_web.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(github_web.GitHubWebError) as ctx:
                github_web.latest_tag("x/y")
        self.assertIn("500", str(ctx.exception))


class FetchRemoteWithoutApiTests(unittest.TestCase):
    def test_uses_web_pages(self) -> None:
        assets = [
            github_web.WebAsset(
                name="zapret2-v1.0.8.tar.gz",
                url="https://github.com/bol-van/zapret2/releases/download/v1.0.8/zapret2-v1.0.8.tar.gz",
                sha256="f" * 64,
            ),
            github_web.WebAsset(
                name="zapret2-v1.0.8-openwrt-embedded.tar.gz",
                url="https://example.com/openwrt",
                sha256="e" * 64,
            ),
        ]
        with mock.patch.object(updates.github_web, "latest_tag", return_value="v1.0.8"):
            with mock.patch.object(
                updates.github_web, "release_assets", return_value=assets
            ):
                with mock.patch.object(
                    updates.tgws, "latest_version", return_value="v1.12.0"
                ):
                    remote = updates.fetch_remote()

        self.assertEqual(remote.zapret_tag, "v1.0.8")
        self.assertEqual(remote.zapret_asset, "zapret2-v1.0.8.tar.gz")
        self.assertEqual(remote.zapret_asset_sha256, "f" * 64)
        self.assertEqual(remote.tgws_tag, "v1.12.0")

    def test_reports_web_error(self) -> None:
        with mock.patch.object(
            updates.github_web,
            "latest_tag",
            side_effect=github_web.GitHubWebError("нет сети"),
        ):
            with mock.patch.object(
                updates.tgws,
                "latest_version",
                side_effect=tgws.TgwsError("нет сети"),
            ):
                with self.assertRaises(updates.UpdateError) as ctx:
                    updates.fetch_remote()
        self.assertIn("нет сети", str(ctx.exception))


class TgwsReleaseFromWebTests(unittest.TestCase):
    def test_fetch_release_builds_assets(self) -> None:
        assets = [
            github_web.WebAsset(
                name="TgWsProxy_linux_amd64",
                url="https://github.com/Flowseal/tg-ws-proxy/releases/download/v1.12.0/TgWsProxy_linux_amd64",
                sha256="a" * 64,
                size=123,
            )
        ]
        with mock.patch.object(tgws.github_web, "latest_tag", return_value="v1.12.0"):
            with mock.patch.object(
                tgws.github_web, "release_assets", return_value=assets
            ):
                release = tgws.fetch_release()
        asset = tgws.select_linux_asset(release)
        self.assertEqual(release.tag, "v1.12.0")
        self.assertEqual(asset.sha256, "a" * 64)
        self.assertEqual(asset.size, 123)


if __name__ == "__main__":
    unittest.main()
