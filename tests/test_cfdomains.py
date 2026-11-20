from __future__ import annotations

import unittest

from linfix import cfdomains


class DecodeVectorTests(unittest.TestCase):
    VECTORS = {
        "virkgj.com": "pclead.co.uk",
        "vmmzovy.com": "offshor.co.uk",
        "mkuosckvso.com": "cakeisalie.co.uk",
        "zaewayzmplad.com": "noskomnadzor.co.uk",
        "khgrre.com": "ebally.co.uk",
    }

    def test_spec_vectors(self) -> None:
        for encoded, expected in self.VECTORS.items():
            with self.subTest(encoded=encoded):
                self.assertEqual(cfdomains.decode_cf_domain(encoded), expected)

    def test_non_com_is_passthrough(self) -> None:
        self.assertEqual(cfdomains.decode_cf_domain("nothingthere"), "nothingthere")
        self.assertEqual(cfdomains.decode_cf_domain("example.co.uk"), "example.co.uk")
        self.assertEqual(cfdomains.decode_cf_domain(""), "")

    def test_letters_and_digits(self) -> None:
        # shift = 3 (a,b,c); цифры и дефис не сдвигаются
        self.assertEqual(cfdomains.decode_cf_domain("abc-123.com"), "xyz-123.co.uk")

    def test_uppercase_preserved(self) -> None:
        # Алгоритм из SPEC чувствителен к суффиксу .com; верхний регистр
        # обрабатывается отдельно и не должен ломать декодер.
        self.assertEqual(cfdomains.decode_cf_domain("ABC.com"), "XYZ.co.uk")


class DecodeListTests(unittest.TestCase):
    def test_valid_list(self) -> None:
        result = cfdomains.decode_encoded_list(["virkgj.com", "vmmzovy.com"])
        self.assertTrue(result.ok)
        self.assertEqual(result.domains, ("pclead.co.uk", "offshor.co.uk"))
        self.assertEqual(result.invalid_count, 0)

    def test_deduplicates(self) -> None:
        result = cfdomains.decode_encoded_list(["virkgj.com", "virkgj.com"])
        self.assertEqual(result.domains, ("pclead.co.uk",))
        self.assertEqual(result.total, 2)

    def test_minority_invalid_still_ok(self) -> None:
        result = cfdomains.decode_encoded_list(["virkgj.com", "vmmzovy.com", "bad_domain"])
        self.assertTrue(result.ok)
        self.assertEqual(result.invalid_count, 1)

    def test_majority_invalid_is_broken(self) -> None:
        result = cfdomains.decode_encoded_list(["bad_domain", "bad domain", "-x.com"])
        self.assertFalse(result.ok)
        self.assertEqual(result.invalid_count, 3)

    def test_empty_is_not_ok(self) -> None:
        result = cfdomains.decode_encoded_list([])
        self.assertFalse(result.ok)
        self.assertEqual(result.domains, ())


class ParseTextTests(unittest.TestCase):
    def test_comments_blank_bom_crlf(self) -> None:
        text = "\ufeff# comment\r\n\r\n  PCLEAD.CO.UK  \r\n# another\r\noffshor.co.uk\r\n"
        self.assertEqual(
            cfdomains.parse_domain_text(text),
            ["pclead.co.uk", "offshor.co.uk"],
        )

    def test_inline_hash_is_not_treated_as_comment(self) -> None:
        # Формат hostlist: комментарий только в начале строки.
        self.assertEqual(cfdomains.parse_domain_text("a.example # note\n"), ["a.example # note"])


class ValidationTests(unittest.TestCase):
    def test_valid(self) -> None:
        for value in ("pclead.co.uk", "sub.domain.co.uk", "a-b.example.com", "123.co.uk"):
            with self.subTest(value=value):
                self.assertTrue(cfdomains.is_valid_domain(value))

    def test_invalid(self) -> None:
        for value in ("PCLEAD.CO.UK", "-bad.com", "bad-.com", "a.b", "example.c", "bad_domain", ""):
            with self.subTest(value=value):
                self.assertFalse(cfdomains.is_valid_domain(value))

    def test_normalize(self) -> None:
        self.assertEqual(cfdomains.normalize_domain("  PCLEAD.CO.UK. "), "pclead.co.uk")


class DomainsFromTextTests(unittest.TestCase):
    def test_good_source(self) -> None:
        source = cfdomains.domains_from_text("virkgj.com\nvmmzovy.com\n")
        self.assertTrue(source.ok)
        self.assertEqual(source.source, "github")
        self.assertEqual(source.domains, ("pclead.co.uk", "offshor.co.uk"))

    def test_broken_source(self) -> None:
        source = cfdomains.domains_from_text("bad_domain\nbad domain\n- x\n")
        self.assertFalse(source.ok)
        self.assertTrue(source.warnings)
        self.assertGreater(source.invalid_count, source.total / 2)


class FetchSecurityTests(unittest.TestCase):
    def test_http_is_rejected(self) -> None:
        with self.assertRaises(ValueError):
            cfdomains.fetch_text("http://example.com/cfproxy-domains.txt", timeout=1)


class FallbackTests(unittest.TestCase):
    def test_fallback_is_complete(self) -> None:
        domains = cfdomains.FALLBACK_DOMAINS
        self.assertEqual(len(domains), 20)
        self.assertEqual(len(set(domains)), 20)
        self.assertTrue(all(cfdomains.is_valid_domain(domain) for domain in domains))
        self.assertIn("pclead.co.uk", domains)
        self.assertIn("nothingthere.co.uk", domains)

    def test_offline_source(self) -> None:
        source = cfdomains.load_domain_source(offline=True)
        self.assertEqual(source.source, "fallback")
        self.assertEqual(source.domains, cfdomains.FALLBACK_DOMAINS)
        self.assertTrue(source.warnings)


if __name__ == "__main__":
    unittest.main()
