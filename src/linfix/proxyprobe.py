"""Проверка пути через TgWsProxy настоящим MTProto-запросом.

Зачем отдельно от прямой проверки: DC Telegram в этой сети закрыты на уровне
TCP, поэтому «напрямую» всегда BLOCK. Работает только путь через локальный
прокси, и проверять его надо тем же протоколом, что и клиент.

Что делает проба:

1. SOCKS5 CONNECT на ``127.0.0.1:1080`` (TgWsProxy слушает как SOCKS5).
2. Obfuscated2-хендшейк с секретом прокси (64 байта) — иначе прокси не
   признает нас клиентом и не станет ничего передавать.
3. Шифрует ``req_pq_multi`` тем же ключом и ждёт ``resPQ`` от DC.
4. Расшифровывает ответ. Если ``resPQ`` разобран — путь через прокси живой.

Ничего не меняет: только чтение/запрос.
"""

from __future__ import annotations

import hashlib
import os
import socket
import struct
import time
from dataclasses import dataclass

try:
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
except ImportError:  # pragma: no cover - cryptography есть в зависимостях
    Cipher = algorithms = modes = None  # type: ignore[assignment]

PROXY_OK = "ok"
PROXY_DOWN = "proxy_down"
PROXY_TIMEOUT = "timeout"
PROXY_BAD = "bad_response"

SKIP_LEN = 8
PREKEY_LEN = 32
IV_LEN = 16
HANDSHAKE_LEN = 64
PROTO_TAG_POS = 56
DC_IDX_POS = 60

#: Abridged-протокол: тег 0xefefefef, DC2.
PROTO_TAG_ABRIDGED = b"\xef\xef\xef\xef"
#: Ответ DC на req_pq — resPQ.
RES_PQ = 0x05162463
#: Минимальный MTProto-запрос, на который DC всегда отвечает.
REQ_PQ_MULTI = 0xbe7e8ef1


@dataclass
class ProxyProbe:
    ok: bool
    status: str
    latency_ms: int = 0
    detail: str = ""


def _build_handshake(secret: bytes, dc_idx: int = 2) -> tuple[bytes, object, object]:
    """Собрать клиентский obfuscated2 init для MTProto-прокси с секретом.

    Ключ хендшейка и исходящего трафика — ``sha256(prekey + secret)``; для
    входящего берётся перевёрнутый prekey+iv. Оба потока «прокручивают»
    64 байта init, как это делает прокси.
    """

    while True:
        rnd = bytearray(os.urandom(HANDSHAKE_LEN))
        if rnd[0] in (0xEF, 0xEE, 0xDD):
            continue
        if bytes(rnd[:4]) in (
            b"HEAD", b"POST", b"GET ", b"OPTI",
            b"\x16\x03\x01\x02", b"\x16\x03\x03\x02",
        ):
            continue
        if bytes(rnd[4:8]) == b"\x00\x00\x00\x00":
            continue
        break
    rnd_bytes = bytes(rnd)

    prekey_iv = rnd_bytes[SKIP_LEN:SKIP_LEN + PREKEY_LEN + IV_LEN]
    prekey = prekey_iv[:PREKEY_LEN]
    iv = prekey_iv[PREKEY_LEN:]

    key = hashlib.sha256(prekey + secret).digest()

    # Шифруем хвост init тем же ключом, что и трафик.
    tail_cipher = Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor()
    encrypted = tail_cipher.update(rnd_bytes)
    keystream_tail = bytes(
        encrypted[i] ^ rnd_bytes[i]
        for i in range(PROTO_TAG_POS, HANDSHAKE_LEN)
    )
    tail_plain = PROTO_TAG_ABRIDGED + struct.pack("<h", dc_idx) + os.urandom(2)
    encrypted_tail = bytes(
        tail_plain[i] ^ keystream_tail[i] for i in range(8)
    )
    result = bytearray(rnd_bytes)
    result[PROTO_TAG_POS:HANDSHAKE_LEN] = encrypted_tail

    # Исходящий поток: тот же ключ/iv, пропуск 64 байт init (их уже
    # «потратил» хендшейк).
    enc = Cipher(algorithms.AES(key), modes.CTR(iv)).encryptor()
    enc.update(b"\x00" * HANDSHAKE_LEN)

    # Входящий: перевёрнутый prekey+iv, без пропуска — прокси шифрует ответ
    # этим потоком с самого начала.
    rev = prekey_iv[::-1]
    dec_key = hashlib.sha256(rev[:PREKEY_LEN] + secret).digest()
    dec = Cipher(algorithms.AES(dec_key), modes.CTR(rev[PREKEY_LEN:])).encryptor()

    return bytes(result), enc, dec


def _read_exact(sock: socket.socket, size: int) -> bytes:
    data = b""
    while len(data) < size:
        chunk = sock.recv(size - len(data))
        if not chunk:
            break
        data += chunk
    return data


def probe_proxy(
    host: str = "127.0.0.1",
    port: int = 1080,
    *,
    secret_hex: str = "",
    dc_ip: str = "149.154.167.51",
    timeout: float = 6.0,
) -> ProxyProbe:
    """Отправить req_pq через прокси и дождаться resPQ."""

    started = time.monotonic()
    try:
        secret = bytes.fromhex(secret_hex.removeprefix("dd"))
    except ValueError:
        secret = b""
    if len(secret) != 16:
        return ProxyProbe(False, PROXY_BAD, 0, "нет секрета прокси в конфиге")
    if Cipher is None:
        return ProxyProbe(False, PROXY_BAD, 0, "нет python3-cryptography")

    try:
        # TgWsProxy — это MTProto-прокси, а не SOCKS5: клиент подключается
        # напрямую и сразу шлёт obfuscated2-хендшейк.
        with socket.create_connection((host, port), timeout=timeout) as sock:
            handshake, enc, dec = _build_handshake(secret)
            sock.sendall(handshake)

            # Unencrypted MTProto-сообщение:
            #   auth_key_id(8=0) + msg_id(8) + msg_length(4) + req_pq_multi + nonce(16)
            inner = struct.pack("<I", REQ_PQ_MULTI) + os.urandom(16)
            body = (
                b"\x00" * 8
                + struct.pack("<Q", int(time.time() * 2**32) & ~3)
                + struct.pack("<I", len(inner))
                + inner
            )
            # Abridged: 1 байт длины в 4-байтовых словах, затем тело.
            words = len(body) // 4
            sock.sendall(enc.update(bytes([words])))
            sock.sendall(enc.update(body))

            sock.settimeout(timeout)
            # Сначала 1 байт длины (abridged, в 4-байтовых словах), затем тело.
            head = _read_exact(sock, 1)
            if not head:
                return ProxyProbe(
                    False, PROXY_BAD, int((time.monotonic() - started) * 1000),
                    "DC не ответил через прокси",
                )
            first = dec.update(head)[0]
            words = first & 0x7F
            extra = b""
            if words == 0x7F:
                extra = dec.update(_read_exact(sock, 3))
                words = int.from_bytes(extra, "little")
            body_len = words * 4
            if body_len <= 0 or body_len > 8192:
                return ProxyProbe(
                    False, PROXY_BAD, int((time.monotonic() - started) * 1000),
                    f"некорректная длина ответа: {body_len}",
                )
            encrypted = _read_exact(sock, body_len)
            plain = dec.update(encrypted)
    except ConnectionRefusedError:
        return ProxyProbe(False, PROXY_DOWN, 0, "порт прокси не слушается")
    except (socket.timeout, TimeoutError):
        return ProxyProbe(
            False, PROXY_TIMEOUT, int((time.monotonic() - started) * 1000),
            "таймаут ответа через прокси",
        )
    except OSError as exc:
        return ProxyProbe(
            False, PROXY_DOWN, int((time.monotonic() - started) * 1000), str(exc)
        )
    except Exception as exc:  # noqa: BLE001
        return ProxyProbe(
            False, PROXY_BAD, int((time.monotonic() - started) * 1000), str(exc)
        )

    elapsed = int((time.monotonic() - started) * 1000)
    # resPQ идёт после auth_key_id(8) + msg_id(8) + msg_length(4) = 20 байт.
    if len(plain) >= 24 and struct.unpack("<I", plain[20:24])[0] == RES_PQ:
        return ProxyProbe(True, PROXY_OK, elapsed, "resPQ получен")
    return ProxyProbe(False, PROXY_BAD, elapsed, "ответ не похож на resPQ")
