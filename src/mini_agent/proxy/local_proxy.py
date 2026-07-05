"""纯 Python 版本的"本地代理进程"。

和之前依赖 xray-core 子进程的版本相比,这里是在 asyncio 事件循环里直接起一个
SOCKS5 server task,收到本地连接后按节点协议(ss / trojan)转发——不再需要
任何外部可执行文件,也没有子进程管理的开销。

支持: ss(经典 AEAD)、trojan。
不支持: vmess / vless(协议更复杂,纯 Python 重实现性价比不高,遇到时直接
在验证阶段跳过,不会假装可用)。
"""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass

from .protocols import ShadowsocksConnector, TrojanConnector
from .subscription import ProxyNode
from .xray_runner import find_free_port  # 复用找空闲端口的小工具

SUPPORTED_PROTOCOLS = {"ss", "trojan"}


def _build_connector(node: ProxyNode):
    if node.protocol == "ss":
        p = node.params
        return ShadowsocksConnector(node.server, node.port, p["method"], p["password"])
    if node.protocol == "trojan":
        p = node.params
        return TrojanConnector(
            node.server,
            node.port,
            p.get("_userinfo", ""),
            sni=p.get("sni") or p.get("peer"),
            allow_insecure=p.get("allowInsecure") in ("1", "true", True),
        )
    raise ValueError(f"protocol '{node.protocol}' not supported without xray")


async def _handshake_no_auth(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    ver_nm = await reader.readexactly(2)
    nmethods = ver_nm[1]
    await reader.readexactly(nmethods)
    # 回复: 版本5, 选择 method 0x00 (无需认证)
    writer.write(b"\x05\x00")
    await writer.drain()


async def _read_connect_request(reader: asyncio.StreamReader) -> tuple[str, int]:
    header = await reader.readexactly(4)  # ver, cmd, rsv, atyp
    atyp = header[3]
    if atyp == 0x01:  # IPv4
        addr_bytes = await reader.readexactly(4)
        host = ".".join(str(b) for b in addr_bytes)
    elif atyp == 0x03:  # 域名
        length = (await reader.readexactly(1))[0]
        host = (await reader.readexactly(length)).decode("ascii", errors="ignore")
    elif atyp == 0x04:  # IPv6
        addr_bytes = await reader.readexactly(16)
        host = ":".join(f"{addr_bytes[i]:02x}{addr_bytes[i+1]:02x}" for i in range(0, 16, 2))
    else:
        raise ValueError(f"unsupported SOCKS5 ATYP: {atyp}")
    (port,) = struct.unpack(">H", await reader.readexactly(2))
    return host, port


async def _reply_success(writer: asyncio.StreamWriter) -> None:
    # ver=5, rep=0(成功), rsv=0, atyp=1(ipv4), bnd.addr=0.0.0.0, bnd.port=0
    writer.write(b"\x05\x00\x00\x01\x00\x00\x00\x00\x00\x00")
    await writer.drain()


async def _reply_failure(writer: asyncio.StreamWriter) -> None:
    writer.write(b"\x05\x01\x00\x01\x00\x00\x00\x00\x00\x00")
    await writer.drain()


async def _pipe_raw(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.proxy.local_proxy')
        pass
    finally:
        writer.close()


async def _relay_trojan(client_reader, client_writer, connector: TrojanConnector, host: str, port: int) -> None:
    remote_reader, remote_writer = await connector.open(host, port)
    await _reply_success(client_writer)
    await asyncio.gather(
        _pipe_raw(client_reader, remote_writer),
        _pipe_raw(remote_reader, client_writer),
    )


async def _relay_shadowsocks(client_reader, client_writer, connector: ShadowsocksConnector, host: str, port: int) -> None:
    _, _, session = await connector.open(host, port)
    await _reply_success(client_writer)

    async def _client_to_remote():
        try:
            while True:
                data = await client_reader.read(65536)
                if not data:
                    break
                await session.send(data)
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.proxy.local_proxy')
            pass

    async def _remote_to_client():
        try:
            while True:
                data = await session.recv()
                if not data:
                    break
                client_writer.write(data)
                await client_writer.drain()
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.proxy.local_proxy')
            pass
        finally:
            client_writer.close()

    await asyncio.gather(_client_to_remote(), _remote_to_client())


async def _handle_client(client_reader, client_writer, node: ProxyNode) -> None:
    try:
        await _handshake_no_auth(client_reader, client_writer)
        host, port = await _read_connect_request(client_reader)
        if node.protocol == "ss":
            connector = _build_connector(node)
            await _relay_shadowsocks(client_reader, client_writer, connector, host, port)
        elif node.protocol == "trojan":
            connector = _build_connector(node)
            await _relay_trojan(client_reader, client_writer, connector, host, port)
        else:
            await _reply_failure(client_writer)
            client_writer.close()
    except Exception:
        try:
            client_writer.close()
        except Exception as _mini_agent_exc:
            from mini_agent.errors import log_exception
            log_exception(_mini_agent_exc, where='mini_agent.proxy.local_proxy')
            pass


@dataclass
class RunningProxy:
    node: ProxyNode
    local_port: int
    server: asyncio.AbstractServer

    @property
    def socks_url(self) -> str:
        return f"socks5://127.0.0.1:{self.local_port}"

    async def stop(self) -> None:
        self.server.close()
        await self.server.wait_closed()


async def start_local_proxy(node: ProxyNode, local_port: int | None = None) -> RunningProxy:
    if node.protocol not in SUPPORTED_PROTOCOLS:
        raise ValueError(
            f"纯 Python 模式暂不支持协议 '{node.protocol}'(目前只实现了 ss / trojan),"
            "此节点会在验证阶段被跳过。"
        )
    local_port = local_port or find_free_port()

    async def _on_client(r, w):
        await _handle_client(r, w, node)

    server = await asyncio.start_server(_on_client, "127.0.0.1", local_port)
    return RunningProxy(node=node, local_port=local_port, server=server)
