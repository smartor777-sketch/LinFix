from __future__ import annotations

import shlex
import tempfile
import unittest
from pathlib import Path

from linfix import zapret

# Реальная строка процесса nfqws2 с целевой машины (см. docs/INVESTIGATION.md).
CMDLINE = (
    "/opt/zapret2/nfq2/nfqws2 --user=tpws --fwmark=0x40000000 "
    "--lua-init=@/opt/zapret2/lua/zapret-lib.lua "
    "--lua-init=@/opt/zapret2/lua/zapret-antidpi.lua "
    "--lua-init=@/opt/zapret2/lua/zapret-auto.lua --qnum=300 "
    "--filter-tcp=443 --filter-l7=tls /opt/zapret2/ipset/zapret-hosts-rut.txt "
    "--payload=tls_client_hello --lua-desync=tcpseg:pos=0,1:ip_id=rnd:repeats=1 --new "
    "--filter-tcp=443 --filter-l7=tls "
    "--hostlist=/opt/zapret2/ipset/zapret-hosts-user.txt "
    "--hostlist=/opt/zapret2/ipset/zapret-hosts.txt.gz "
    "--hostlist-exclude=/opt/zapret2/ipset/zapret-hosts-user-exclude.txt "
    "--payload=tls_client_hello --lua-desync=multisplit:pos=1:seqovl=568:seqovl_pattern=t4pda --new "
    "--filter-tcp=80 --filter-l7=http "
    "--hostlist=/opt/zapret2/ipset/zapret-hosts-user.txt "
    "--hostlist=/opt/zapret2/ipset/zapret-hosts.txt.gz "
    "--hostlist-exclude=/opt/zapret2/ipset/zapret-hosts-user-exclude.txt "
    "--payload=http_req --lua-desync=http_methodeol --new"
)


class ParseArgsTests(unittest.TestCase):
    def test_real_cmdline(self) -> None:
        parsed = zapret.parse_nfqws_args(shlex.split(CMDLINE))
        self.assertEqual(parsed.qnum, 300)
        self.assertEqual(parsed.fwmark, "0x40000000")
        self.assertEqual(parsed.user, "tpws")
        self.assertEqual(
            parsed.hostlists,
            (
                "/opt/zapret2/ipset/zapret-hosts-user.txt",
                "/opt/zapret2/ipset/zapret-hosts.txt.gz",
                "/opt/zapret2/ipset/zapret-hosts-user.txt",
                "/opt/zapret2/ipset/zapret-hosts.txt.gz",
            ),
        )
        self.assertEqual(
            parsed.hostlist_excludes,
            (
                "/opt/zapret2/ipset/zapret-hosts-user-exclude.txt",
                "/opt/zapret2/ipset/zapret-hosts-user-exclude.txt",
            ),
        )

    def test_space_separated_form(self) -> None:
        parsed = zapret.parse_nfqws_args(["--hostlist", "/x/hosts", "--qnum", "42"])
        self.assertEqual(parsed.hostlists, ("/x/hosts",))
        self.assertEqual(parsed.qnum, 42)

    def test_missing_values(self) -> None:
        parsed = zapret.parse_nfqws_args(["--qnum=oops", "--hostlist"])
        self.assertIsNone(parsed.qnum)
        self.assertEqual(parsed.hostlists, ())


class HostlistChoiceTests(unittest.TestCase):
    def test_prefers_user_file(self) -> None:
        chosen = zapret.choose_hostlist(
            ["/opt/zapret2/ipset/zapret-hosts.txt.gz", "/opt/zapret2/ipset/zapret-hosts-user.txt"]
        )
        self.assertEqual(chosen, "/opt/zapret2/ipset/zapret-hosts-user.txt")

    def test_skips_gz(self) -> None:
        chosen = zapret.choose_hostlist(["/a/base.gz", "/a/other.txt"])
        self.assertEqual(chosen, "/a/other.txt")

    def test_only_gz_fallback(self) -> None:
        chosen = zapret.choose_hostlist(["/a/base.gz"])
        self.assertEqual(chosen, "/a/base.gz")

    def test_empty(self) -> None:
        self.assertIsNone(zapret.choose_hostlist([]))


class InstallDirTests(unittest.TestCase):
    def test_nfq2_layout(self) -> None:
        self.assertEqual(zapret.infer_install_dir("/opt/zapret2/nfq2/nfqws2"), "/opt/zapret2")

    def test_classic_layout(self) -> None:
        self.assertEqual(zapret.infer_install_dir("/opt/zapret/nfqws"), "/opt/zapret")


class HostlistReadTests(unittest.TestCase):
    def test_reads_and_normalizes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "hosts"
            path.write_text("# comment\r\nPCLEAD.CO.UK\r\n\r\noffshor.co.uk\r\n", encoding="utf-8")
            domains = zapret.read_hostlist_domains(path)
        self.assertEqual(domains, {"pclead.co.uk", "offshor.co.uk"})

    def test_missing_file(self) -> None:
        self.assertEqual(zapret.read_hostlist_domains("/nonexistent/hosts"), set())


if __name__ == "__main__":
    unittest.main()


class BareHostlistTests(unittest.TestCase):
    def test_finds_bare_path(self) -> None:
        argv = [
            "--qnum=300",
            "--filter-tcp=443",
            "/opt/zapret2/ipset/zapret-hosts-rut.txt",
            "--payload=tls_client_hello",
        ]
        self.assertEqual(
            zapret.find_bare_hostlist_args(argv),
            ["/opt/zapret2/ipset/zapret-hosts-rut.txt"],
        )

    def test_ignores_proper_flag(self) -> None:
        argv = ["--hostlist=/opt/zapret2/ipset/zapret-hosts-user.txt"]
        self.assertEqual(zapret.find_bare_hostlist_args(argv), [])

    def test_ignores_non_list_paths(self) -> None:
        argv = ["--lua-init=@/opt/zapret2/lua/x.lua", "--user=tpws"]
        self.assertEqual(zapret.find_bare_hostlist_args(argv), [])

    def test_config_problems(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "config"
            path.write_text(
                'NFQWS2_OPT="\n'
                "--filter-tcp=443 --filter-l7=tls /opt/zapret2/ipset/zapret-hosts-rut.txt "
                "--payload=tls_client_hello --new\n"
                "--filter-tcp=443 --hostlist=/opt/zapret2/ipset/zapret-hosts-user.txt --new\n"
                '"\n',
                encoding="utf-8",
            )
            problems = zapret.config_hostlist_problems(path)
        self.assertEqual(len(problems), 1)
        self.assertIn("zapret-hosts-rut.txt", problems[0])


class NeutralizeTests(unittest.TestCase):
    def _write(self, text: str) -> Path:
        import tempfile
        from pathlib import Path as P

        tmp = tempfile.mkdtemp()
        path = P(tmp) / "config"
        path.write_text(text, encoding="utf-8")
        return path

    def test_comments_bare_hostlist_line(self) -> None:
        path = self._write(
            'NFQWS2_OPT="\n'
            "--filter-tcp=443 --filter-l7=tls /opt/zapret2/ipset/zapret-hosts-rut.txt "
            "--payload=tls_client_hello --new\n"
            '"\n'
        )
        from linfix import helper
        from types import SimpleNamespace

        code, payload = helper.action_zapret_neutralize_hostlist_args(
            SimpleNamespace(config_path=str(path), json=False)
        )
        self.assertEqual(code, 0)
        self.assertTrue(payload["changed"])
        text = path.read_text(encoding="utf-8")
        self.assertIn("# --filter-tcp=443", text)
        self.assertIn("# linfix: профиль без --hostlist обезврежен", text)
        # Ключевое: мы НЕ включаем профиль.
        self.assertNotIn("\n--filter-tcp=443 --filter-l7=tls /opt", text)

    def test_correct_line_untouched(self) -> None:
        path = self._write(
            "--filter-tcp=443 --filter-l7=tls "
            "--hostlist=/opt/zapret2/ipset/zapret-hosts-rut.txt --new\n"
        )
        from linfix import helper
        from types import SimpleNamespace

        code, payload = helper.action_zapret_neutralize_hostlist_args(
            SimpleNamespace(config_path=str(path), json=False)
        )
        self.assertEqual(code, 0)
        self.assertFalse(payload["changed"])

    def test_restore_from_backup(self) -> None:
        import tempfile
        from pathlib import Path as P
        from linfix import helper
        from types import SimpleNamespace

        tmp = P(tempfile.mkdtemp())
        target = tmp / "config"
        target.write_text("новое\n", encoding="utf-8")
        backup = tmp / "config.bak"
        backup.write_text("старое\n", encoding="utf-8")

        code, payload = helper.action_zapret_restore_config(
            SimpleNamespace(backup=str(backup), config_path=str(target), json=False)
        )
        self.assertEqual(code, 0)
        self.assertEqual(target.read_text(encoding="utf-8"), "старое\n")
