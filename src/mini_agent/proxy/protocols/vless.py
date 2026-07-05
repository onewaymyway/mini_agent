"""纯 Python 实现的 VLESS 客户端。

VLESS 协议本身刻意做得很简单:请求头(版本+UUID+可选附加信息+目标地址)是明文的,
安全性完全依赖外层传输层(通常是 TLS,有时套一层 WebSocket 用来过 CDN)。
这也是我们优先支持 VLESS 而不是 VMess 的原因: VMess 有一套自定义的 AEAD 头部
加密/鉴权(自定义 KDF 链 + AES-ECB 认证 ID + 长度域加密),没有真实服务端可供
反复联调的情况下手搓这套东西风险很高,容易做出一个"看起来对但实际连不通"的实现；
VLESS 的头部没有这层加密,出错空间小很多。

支持: network = tcp / ws , security = tls / none(不支持 reality/xtls-vision flow,
遇到 flow 非空会给出警告并按普通模式尝试——不保证能连通)。
"""

from __future__ import annotations

import asyncio
import os
import ssl
import struct
import uuid as uuid_mod

from .shadowsocks import encode_socks5_address


def _ssl_context(allow_insecure: bool) -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if allow_insecure:
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    return ctx


class _WebSocketWrapper:
    """极简 WebSocket 客户端封装,只实现 VLESS/VMess-over-ws 需要的那部分:
    握手 + 二进制帧(客户端发送侧必须做掩码,这是 RFC6455 强制要求)。
    """

    def __init__(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter, host: str, path: str):
        self.reader = reader
        self.writer = writer
        self.host = host
        self.path = path or "/"
        self._recv_buf = b""

    async def handshake(self) -> None:
        key = base64_key = __import__("base64").b64encode(os.urandom(16)).decode()
        req = (
            f"GET {self.path} HTTP/1.1\r\n"
            f"Host: {self.host}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n"
            f"\r\n"
        ).encode()
        self.writer.write(req)
        await self.writer.drain()
        # 读到 \r\n\r\n 为止(响应头)
        buf = b""
        while b"\r\n\r\n" not in buf:
            chunk = await self.reader.read(4096)
            if not chunk:
                raise ConnectionError("websocket handshake failed: connection closed")
            buf += chunk
        header, _, rest = buf.partition(b"\r\n\r\n")
        if b"101" not in header.split(b"\r\n", 1)[0]:
            raise ConnectionError(f"websocket handshake rejected: {header[:200]!r}")
        self._recv_buf = rest  # 握手响应之后可能已经带了数据帧

    @staticmethod
    def _encode_frame(data: bytes, opcode: int = 0x2) -> bytes:
        mask = os.urandom(4)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        length = len(data)
        if length < 126:
            header = struct.pack("!BB", 0x80 | opcode, 0x80 | length)
        elif length < 65536:
            header = struct.pack("!BBH", 0x80 | opcode, 0x80 | 126, length)
        else:
            header = struct.pack("!BBQ", 0x80 | opcode, 0x80 | 127, length)
        return header + mask + masked

    async def send(self, data: bytes) -> None:
        self.writer.write(self._encode_frame(data))
        await self.writer.drain()

    async def _read_exact_from_buf_or_sock(self, n: int) -> bytes:
        while len(self._recv_buf) < n:
            chunk = await self.reader.read(65536)
            if not chunk:
                break
            self._recv_buf += chunk
        data, self._recv_buf = self._recv_buf[:n], self._recv_buf[n:]
        return data

    async def recv(self) -> bytes:
        head = await self._read_exact_from_buf_or_sock(2)
        if len(head) < 2:
            return b""
        b0, b1 = head[0], head[1]
        opcode = b0 & 0x0F
        masked = bool(b1 & 0x80)
        length = b1 & 0x7F
        if length == 126:
            ext = await self._read_exact_from_buf_or_sock(2)
            (length,) = struct.unpack("!H", ext)
        elif length == 127:
            ext = await self._read_exact_from_buf_or_sock(8)
            (length,) = struct.unpack("!Q", ext)
        mask_key = await self._read_exact_from_buf_or_sock(4) if masked else b""
        payload = await self._read_exact_from_buf_or_sock(length)
        if masked:
            payload = bytes(b ^ mask_key[i % 4] for i, b in enumerate(payload))
        if opcode == 0x8:  # close
            return b""
        if opcode in (0x1, 0x2):
            return payload
        # ping/pong 等控制帧: 忽略后继续读下一帧
        return await self.recv()


class VlessConnector:
    def __init__(self, server: str, port: int, params: dict):
        self.server = server
        self.port = port
        self.uuid = str(uuid_mod.UUID(params.get("_userinfo", "")))
        self.network = params.get("type", "tcp")
        self.security = params.get("security", "none")
        self.sni = params.get("sni") or params.get("host") or server
        self.ws_path = params.get("path", "/")
        self.ws_host = params.get("host") or self.sni
        self.allow_insecure = params.get("allowInsecure") in ("1", "true", True)
        self.flow = params.get("flow", "")

    def _build_request_header(self, target_host: str, target_port: int) -> bytes:
        uuid_bytes = uuid_mod.UUID(self.uuid).bytes
        addr = encode_socks5_address(target_host, target_port)
        # VLESS 地址格式和 SOCKS5 的字段顺序不同: port 在 atyp/addr 之前
        atyp = addr[0]
        addr_body = addr[1:-2]
        port_bytes = addr[-2:]
        return (
            b"\x00"  # version
            + uuid_bytes
            + b"\x00"  # addons length = 0 (无附加信息)
            + b"\x01"  # command: 0x01 = TCP
            + port_bytes
            + bytes([atyp])
            + addr_body
        )

    async def open(self, target_host: str, target_port: int):
        """返回一个统一的 (send, recv, close) 接口,屏蔽 tcp/ws 的差异。"""
        if self.flow:
            import logging

            logging.getLogger(__name__).warning(
                "VLESS 节点声明了 flow='%s'(通常是 xtls-rprx-vision),纯 Python 实现不支持,"
                "会按普通模式尝试,大概率连不通。", self.flow,
            )

        header = self._build_request_header(target_host, target_port)

        if self.network == "ws":
            reader, writer = await asyncio.open_connection(
                self.server, self.port,
                ssl=_ssl_context(self.allow_insecure) if self.security == "tls" else None,
                server_hostname=self.sni if self.security == "tls" else None,
            )
            ws = _WebSocketWrapper(reader, writer, self.ws_host, self.ws_path)
            await ws.handshake()
            await ws.send(header)

            async def send(data: bytes) -> None:
                await ws.send(data)

            async def recv() -> bytes:
                return await ws.recv()

            def close() -> None:
                writer.close()

            return send, recv, close

        # network == "tcp"(默认): 原始 TCP(+可选 TLS),头部之后直接是裸数据流
        reader, writer = await asyncio.open_connection(
            self.server, self.port,
            ssl=_ssl_context(self.allow_insecure) if self.security == "tls" else None,
            server_hostname=self.sni if self.security == "tls" else None,
        )
        writer.write(header)
        await writer.drain()

        # VLESS 响应头: 1字节version + 1字节addons长度 + addons,读掉之后剩下的才是数据
        resp_head = await reader.readexactly(2)
        addons_len = resp_head[1]
        if addons_len:
            await reader.readexactly(addons_len)

        async def send(data: bytes) -> None:
            writer.write(data)
            await writer.drain()

        async def recv() -> bytes:
            return await reader.read(65536)

        def close() -> None:
            writer.close()

        return send, recv, close
