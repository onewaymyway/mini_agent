"""独立代理服务(可选)。

思路: ProxyPool 已经在本地维护了若干"常驻可用"的 SOCKS5 端口。
这里额外起一个很薄的 FastAPI 服务,只做两件事:
  1. 暴露 /status /best /rotate 接口,方便其它进程(甚至其它语言写的应用)
     查询"现在该用哪个本地端口"。
  2. 可选地跑一个基于 asyncio 的 TCP 转发层,对外暴露一个"固定端口"
     (比如 1080),背后自动转发到当前 best 节点的本地端口——这样别的应用
     只需要把代理设置成 127.0.0.1:1080 一次性配好,永远不用管背后节点怎么切换。

这就是"专门代理服务"的落地方式,不需要做 TUN/透明代理那么重的方案:
只要目标应用支持"配置一个 HTTP_PROXY/SOCKS 代理"(几乎所有抓取库、大部分
系统都支持),就可以做到对使用方"基本无感"——配一次,后面节点怎么换、
怎么测活,应用完全不用关心。
"""

from __future__ import annotations

import asyncio

from fastapi import FastAPI

from .pool import ProxyPool

app = FastAPI(title="mini-agent proxy pool service")
_pool: ProxyPool | None = None


def bind_pool(pool: ProxyPool) -> None:
    global _pool
    _pool = pool


@app.get("/status")
async def status():
    if _pool is None:
        return {"entries": [], "error": "pool not bound"}
    return {"entries": _pool.status()}


@app.get("/best")
async def best():
    return {"socks_url": _pool.get_best_socks_url() if _pool else None}


@app.get("/rotate")
async def rotate():
    return {"socks_url": _pool.get_rotating_socks_url() if _pool else None}


async def _pipe(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    try:
        while True:
            data = await reader.read(65536)
            if not data:
                break
            writer.write(data)
            await writer.drain()
    except Exception as _mini_agent_exc:
        from mini_agent.errors import log_exception
        log_exception(_mini_agent_exc, where='mini_agent.proxy.service')
        pass
    finally:
        writer.close()


async def _handle_conn(client_reader, client_writer, get_upstream_port):
    port = get_upstream_port()
    if port is None:
        client_writer.close()
        return
    try:
        upstream_reader, upstream_writer = await asyncio.open_connection("127.0.0.1", port)
    except Exception:
        client_writer.close()
        return
    await asyncio.gather(
        _pipe(client_reader, upstream_writer),
        _pipe(upstream_reader, client_writer),
    )


async def run_fixed_entry_forwarder(pool: ProxyPool, listen_port: int = 1080) -> asyncio.AbstractServer:
    """起一个固定端口(如 1080),透明转发到当前 pool.get_best_socks_url() 对应的本地端口。

    这样任何应用只需要把代理配置指向 127.0.0.1:1080 一次,
    节点池内部怎么换节点对它完全透明——效果上类似"应用无感知的代理切换"。
    """

    def _get_port() -> int | None:
        url = pool.get_best_socks_url()
        if not url:
            return None
        return int(url.rsplit(":", 1)[-1])

    async def _on_conn(r, w):
        await _handle_conn(r, w, _get_port)

    return await asyncio.start_server(_on_conn, "127.0.0.1", listen_port)
