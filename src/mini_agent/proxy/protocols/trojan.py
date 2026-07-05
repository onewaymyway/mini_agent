"""纯 Python 实现的 Trojan 客户端。

Trojan 协议非常简单,本质是"看起来完全像普通 HTTPS 流量的转发协议":
1. 用标准 TLS 连接到服务端(所以天然抗 DPI 识别,伪装成正常 HTTPS)。
2. TLS 建立后,第一个请求包格式为:
     SHA224(password) 的十六进制(56字节) + "\r\n"
     + 1字节 CMD(0x01=CONNECT)
     + SOCKS5 地址格式(ATYP+ADDR+PORT)
     + "\r\n"
     + 后续原始应用层数据(可以和上面一起发,也可以分开发)
3. 之后就是纯粹的双向字节转发,没有额外加密层(加密已经由 TLS 提供)。
"""

from __future__ import annotations

import asyncio
import hashlib
import ssl

from .shadowsocks import encode_socks5_address


class TrojanConnector:
    def __init__(self, server: str, port: int, password: str, sni: str | None = None, allow_insecure: bool = False):
        self.server = server
        self.port = port
        self.password_hash = hashlib.sha224(password.encode("utf-8")).hexdigest().encode("ascii")
        self.sni = sni or server
        self.allow_insecure = allow_insecure

    def _ssl_context(self) -> ssl.SSLContext:
        ctx = ssl.create_default_context()
        if self.allow_insecure:
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    async def open(self, target_host: str, target_port: int) -> tuple[asyncio.StreamReader, asyncio.StreamWriter]:
        reader, writer = await asyncio.open_connection(
            self.server, self.port, ssl=self._ssl_context(), server_hostname=self.sni
        )
        addr = encode_socks5_address(target_host, target_port)
        header = self.password_hash + b"\r\n" + b"\x01" + addr + b"\r\n"
        writer.write(header)
        await writer.drain()
        return reader, writer
