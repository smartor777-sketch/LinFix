from __future__ import annotations

import hashlib
import json
import unittest
from unittest import mock

from linfix import proxyprobe


class ProxyProbeTests(unittest.TestCase):
    def test_without_secret_is_bad(self) -> None:
        probe = proxyprobe.probe_proxy(secret_hex="")
        self.assertFalse(probe.ok)
        self.assertEqual(probe.status, proxyprobe.PROXY_BAD)

    def test_proxy_down(self) -> None:
        # Порт 1 почти наверняка закрыт: ожидаем «прокси не слушается».
        probe = proxyprobe.probe_proxy(port=1, secret_hex="00" * 16, timeout=1.0)
        self.assertFalse(probe.ok)
        self.assertIn(probe.status, {proxyprobe.PROXY_DOWN, proxyprobe.PROXY_BAD})

    def test_handshake_contains_tag_and_dc(self) -> None:
        """Собранный init должен расшифровываться обратно в tag+DC."""

        secret = bytes.fromhex("00" * 15 + "01")
        handshake, _enc, _dec = proxyprobe._build_handshake(secret, dc_idx=2)
        self.assertEqual(len(handshake), proxyprobe.HANDSHAKE_LEN)

        prekey_iv = handshake[
            proxyprobe.SKIP_LEN:proxyprobe.SKIP_LEN
            + proxyprobe.PREKEY_LEN
            + proxyprobe.IV_LEN
        ]
        prekey = prekey_iv[: proxyprobe.PREKEY_LEN]
        iv = prekey_iv[proxyprobe.PREKEY_LEN:]
        key = hashlib.sha256(prekey + secret).digest()

        from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

        dec = Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor()
        plain = dec.update(handshake)
        self.assertEqual(
            plain[proxyprobe.PROTO_TAG_POS:proxyprobe.PROTO_TAG_POS + 4],
            proxyprobe.PROTO_TAG_ABRIDGED,
        )
        self.assertEqual(
            int.from_bytes(plain[proxyprobe.DC_IDX_POS:proxyprobe.DC_IDX_POS + 2], "little"),
            2,
        )


if __name__ == "__main__":
    unittest.main()
