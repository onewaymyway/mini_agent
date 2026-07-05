"""纯 Python 实现的 Shadowsocks AEAD 客户端(经典 AEAD,非 2022-blake3 系列)。

协议要点(以 aes-256-gcm / chacha20-ietf-poly1305 为例):
1. 用 password 通过 EVP_BytesToKey(MD5 迭代)派生出定长主密钥 key。
2. 建立 TCP 连接后,先发送随机 salt(长度 = key 长度)。
3. 用 HKDF-SHA1(key, salt, info=b"ss-subkey") 派生出本次会话的 subkey。
4. 之后所有数据都按"分片"发送: 每片 = AEAD(2字节长度) + AEAD(payload)。
   nonce 是一个从 0 开始、每次 AEAD 调用后自增的计数器(小端),
   长度块和内容块共享同一个自增序列。
5. 第一片的 payload 前面还要带上目标地址(SOCKS5 地址格式: ATYP+ADDR+PORT)。

参考实现思路来自 shadowsocks-libev / shadowsocks-rust 的 AEAD 分片规范。
"""

from __future__ import annotations

import asyncio
import hashlib
import struct

from cryptography.hazmat.primitives.ciphers.aead import AESGCM, ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from cryptography.hazmat.primitives import hashes

_METHODS = {
    "aes-128-gcm": (16, 16, AESGCM),
    "aes-256-gcm": (32, 32, AESGCM),
    "chacha20-ietf-poly1305": (32, 32, ChaCha20Poly1305),
    "chacha20-poly1305": (32, 32, ChaCha20Poly1305),
}


def _evp_bytes_to_key(password: str, key_len: int) -> bytes:
    """OpenSSL 风格的 EVP_BytesToKey(MD5,无 salt),shadowsocks 密码派生用的就是这个。"""
    d = d_prev = b""
    while len(d) < key_len:
        d_prev = hashlib.md5(d_prev + password.encode("utf-8")).digest()
        d += d_prev
    return d[:key_len]


def _hkdf_subkey(key: bytes, salt: bytes, key_len: int) -> bytes:
    hkdf = HKDF(algorithm=hashes.SHA1(), length=key_len, salt=salt, info=b"ss-subkey")
    return hkdf.derive(key)


def _nonce(counter: int, nonce_len: int = 12) -> bytes:
    return struct.pack("<Q", counter).ljust(nonce_len, b"\x00")


def encode_socks5_address(host: str, port: int) -> bytes:
    """把目标地址编码成 SOCKS5/SS 通用的地址格式。"""
    try:
        import ipaddress

        ip = ipaddress.ip_address(host)
        if ip.version == 4:
            return b"\x01" + ip.packed + struct.pack(">H", port)
        return b"\x04" + ip.packed + struct.pack(">H", port)
    except ValueError:
        hb = host.encode("idna") if any(ord(c) > 127 for c in host) else host.encode("ascii")
        return b"\x03" + bytes([len(hb)]) + hb + struct.pack(">H", port)


class ShadowsocksConnector:
    """给定 server/port/method/password,负责建立到 ss 服务端的加密连接并做流量转发。"""

    def __init__(self, server: str, port: int, method: str, password: str):
        if method not in _METHODS:
            raise ValueError(f"unsupported shadowsocks method: {method}")
        self.server = server
        self.port = port
        self.method = method
        self.password = password
        self.key_len, self.salt_len, self.aead_cls = _METHODS[method]

    async def open(self, target_host: str, target_port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter, "_SSSession"]:
        reader, writer = await asyncio.open_connection(self.server, self.port)
        session = _SSSession(self, reader, writer)
        await session.handshake(target_host, target_port)
        return reader, writer, session


class _SSSession:
    def __init__(self, connector: ShadowsocksConnector, reader: asyncio.StreamReader, writer: asyncio.StreamWriter):
        self.c = connector
        self.reader = reader
        self.writer = writer
        self.key = _evp_bytes_to_key(connector.password, connector.key_len)
        self._enc_counter = 0
        self._dec_counter = 0
        self._enc_aead = None
        self._dec_aead = None
        self._recv_buf = b""

    async def handshake(self, target_host: str, target_port: int) -> None:
        import os

        salt = os.urandom(self.c.salt_len)
        self.writer.write(salt)
        subkey = _hkdf_subkey(self.key, salt, self.c.key_len)
        self._enc_aead = self.c.aead_cls(subkey)
        first_payload = encode_socks5_address(target_host, target_port)
        await self.send(first_payload)

    def _need_dec_salt(self) -> bool:
        return self._dec_aead is None

    async def _ensure_dec_key(self) -> None:
        if self._dec_aead is not None:
            return
        salt = await self.reader.readexactly(self.c.salt_len)
        subkey = _hkdf_subkey(self.key, salt, self.c.key_len)
        self._dec_aead = self.c.aead_cls(subkey)

    async def send(self, data: bytes) -> None:
        # 单个 TCP 段最大明文块，实际实现常限制在 0x3FFF 以内
        chunk_max = 0x3FFF
        for i in range(0, len(data), chunk_max):
            piece = data[i : i + chunk_max]
            length_bytes = struct.pack(">H", len(piece))
            enc_len = self._enc_aead.encrypt(_nonce(self._enc_counter), length_bytes, None)
            self._enc_counter += 1
            enc_payload = self._enc_aead.encrypt(_nonce(self._enc_counter), piece, None)
            self._enc_counter += 1
            self.writer.write(enc_len + enc_payload)
        await self.writer.drain()

    async def recv(self) -> bytes:
        await self._ensure_dec_key()
        tag_len = 16
        try:
            enc_len = await self.reader.readexactly(2 + tag_len)
        except asyncio.IncompleteReadError:
            return b""
        length_bytes = self._dec_aead.decrypt(_nonce(self._dec_counter), enc_len, None)
        self._dec_counter += 1
        (length,) = struct.unpack(">H", length_bytes)
        enc_payload = await self.reader.readexactly(length + tag_len)
        payload = self._dec_aead.decrypt(_nonce(self._dec_counter), enc_payload, None)
        self._dec_counter += 1
        return payload
