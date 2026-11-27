from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from linfix import api, cli, helper, youtube

SAMPLE_CONFIG = (
    "NFQWS2_ENABLE=1\n"
    'NFQWS2_OPT="\n'
    "--filter-tcp=443 --filter-l7=tls --hostlist=/opt/zapret2/ipset/a.txt "
    "--payload=tls_client_hello --lua-desync=multisplit --new\n"
    '"\n'
    "MODE_FILTER=hostlist\n"
)


class YouTubeProfileTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.config = self.root / "config"
        self.config.write_text(SAMPLE_CONFIG, encoding="utf-8")
        self.state = self.root / "youtube-profile"

    def opts(self) -> list[str]:
        text = self.config.read_text(encoding="utf-8")
        block = text.split('NFQWS2_OPT="\n', 1)[1].split('\n"\n', 1)[0]
        return [line for line in block.splitlines() if line.strip()]

    def set(self, profile: str, *, restart: bool = False) -> dict[str, object]:
        return youtube.set_profile(
            profile,
            config_path=self.config,
            state_path=self.state,
            restart=restart,
        )

    def ensure(self, *, restart: bool = False) -> dict[str, object]:
        return youtube.ensure(
            config_path=self.config,
            state_path=self.state,
            restart=restart,
        )

    def test_rule_alt9_matches_working_windows_preset(self) -> None:
        rule = youtube.rule_line("alt9")
        self.assertNotIn("\n", rule)
        self.assertIn("--hostlist-domains=youtube.com,", rule)
        self.assertIn("googlevideo.com", rule)
        self.assertIn("hostfakesplit:host=www.google.com", rule)
        self.assertIn("tcp_ts=-1000", rule)
        self.assertIn("repeats=4", rule)
        self.assertIn("ip_id=zero", rule)
        self.assertIn("--new", rule)

    def test_rule_alt11_matches_flowseal_preset(self) -> None:
        rules = youtube.rule_lines("alt11")
        self.assertEqual(len(rules), 2)  # ALT11 + компаньон ALT9 для видео
        rule = rules[0]
        self.assertNotIn("\n", rule)
        self.assertIn("--blob=yt_fake_tls:@/opt/zapret2/files/fake/", rule)
        self.assertIn("fake:blob=yt_fake_tls:tcp_ts=-600000:ip_id=zero:repeats=8", rule)
        self.assertIn("multisplit:pos=1:seqovl=681:seqovl_pattern=yt_fake_tls", rule)
        self.assertNotIn("googlevideo.com", rule)
        self.assertIn("--hostlist-domains=googlevideo.com", rules[1])
        self.assertIn("hostfakesplit:host=www.google.com", rules[1])

    def test_validate_profile(self) -> None:
        self.assertEqual(youtube.validate_profile("on"), "alt9")
        self.assertEqual(youtube.validate_profile(" ALT11 "), "alt11")
        self.assertEqual(youtube.validate_profile("off"), "off")
        with self.assertRaises(youtube.UnknownProfile):
            youtube.rule_line("g.co")

    def test_enable_inserts_first_rule_once(self) -> None:
        first = self.set("alt9")
        self.assertTrue(first["changed"])
        self.assertEqual(self.state.read_text().strip(), "alt9")
        self.assertEqual(len([line for line in self.opts() if youtube.is_our_rule(line)]), 1)
        self.assertTrue(youtube.is_our_rule(self.opts()[0]))

        second = self.set("alt9")
        self.assertFalse(second["changed"])
        self.assertEqual(len([line for line in self.opts() if youtube.is_our_rule(line)]), 1)

    def test_switch_replaces_rule_not_appends(self) -> None:
        self.set("alt9")
        result = self.set("alt11")
        self.assertTrue(result["changed"])
        lines = self.opts()
        ours = [line for line in lines if youtube.is_our_rule(line)]
        self.assertEqual(len(ours), 2)
        self.assertIn("--blob=yt_fake_tls:@", ours[0])
        self.assertIn("hostfakesplit:host=www.google.com", ours[1])
        self.assertEqual(ours, youtube.rule_lines("alt11"))

    def test_old_broken_alt11_rule_is_replaced(self) -> None:
        broken = SAMPLE_CONFIG.replace(
            "--lua-desync=multisplit",
            youtube.rule_line("alt11").replace("yt_fake_tls:@", "yt_fake_tls@"),
        )
        self.config.write_text(broken)
        self.state.write_text("alt11\n")
        result = self.ensure()
        self.assertTrue(result["changed"])
        lines = [line for line in self.opts() if youtube.is_our_rule(line)]
        self.assertEqual(len(lines), 2)
        self.assertIn("--blob=yt_fake_tls:@", lines[0])
        self.assertIn("hostfakesplit:host=www.google.com", lines[1])
        self.assertNotIn("--blob=yt_fake_tls@/", self.config.read_text())

    def test_config_profile_prefers_alt11_with_alt9_companion(self) -> None:
        text = "\n".join(youtube.rule_lines("alt11"))
        self.assertEqual(youtube.config_profile(text), "alt11")

    def test_off_removes_rule_and_writes_marker(self) -> None:
        self.set("alt9")
        result = self.set("off")
        self.assertTrue(result["changed"])
        self.assertEqual(self.state.read_text().strip(), "off")
        self.assertNotIn(youtube.RULE_MARKERS["alt9"], self.config.read_text())
        self.assertFalse(self.ensure()["changed"])

    def test_current_profile_migrates_from_config(self) -> None:
        self.config.write_text(
            SAMPLE_CONFIG.replace("--lua-desync=multisplit", youtube.rule_line("alt9"))
        )
        self.assertEqual(
            youtube.current_profile(config_path=self.config, state_path=self.state), "alt9"
        )

    def test_ensure_restores_selected_profile_after_zapret_overwrite(self) -> None:
        self.set("alt11")
        self.config.write_text(SAMPLE_CONFIG)  # как install_easy.sh
        result = self.ensure()
        self.assertTrue(result["changed"])
        self.assertIn("--blob=yt_fake_tls:@", self.config.read_text())
        self.assertFalse(self.ensure()["changed"])

    def test_status_detects_drift(self) -> None:
        self.set("alt9")
        with mock.patch.object(youtube.util, "run", return_value=mock.Mock(stdout="active\n")):
            report = youtube.status(config_path=self.config, state_path=self.state)
        self.assertEqual(report["profile"], "alt9")
        self.assertEqual(report["config_profile"], "alt9")
        self.assertTrue(report["active"])
        self.assertFalse(report["needs_restore"])

        self.config.write_text(SAMPLE_CONFIG)
        with mock.patch.object(youtube.util, "run", return_value=mock.Mock(stdout="active\n")):
            report = youtube.status(config_path=self.config, state_path=self.state)
        self.assertEqual(report["config_profile"], "off")
        self.assertTrue(report["needs_restore"])

    def test_helper_and_api(self) -> None:
        self.assertIn("youtube.set_profile", helper.ALLOWED_ACTIONS)
        args = mock.Mock(profile="alt11")
        with mock.patch.object(youtube, "set_profile", return_value={"ok": True, "profile": "alt11"}) as call:
            code, payload = helper.action_youtube_profile(args)
        self.assertEqual(code, 0)
        self.assertEqual(payload["profile"], "alt11")
        call.assert_called_once_with("alt11")

        with mock.patch.object(api.util, "run_helper", return_value=(0, {"ok": True})) as call:
            code, result = api.set_youtube_profile("alt11")
        self.assertEqual(code, 0)
        call.assert_called_once_with("youtube.set_profile", ["--profile", "alt11"], timeout=120)

    def test_cli_accepts_profiles(self) -> None:
        parser = cli.build_parser()
        for name in ("alt9", "alt11", "off"):
            args = parser.parse_args(["youtube", name])
            self.assertEqual(args.youtube_command, name)


if __name__ == "__main__":
    unittest.main()
