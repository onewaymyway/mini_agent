"""节点可用性验证。

只做 TCP connect 测试是不够的(很多墙内可连但数据不通/被 RST)。
这里的策略是: 起本地 xray 进程 -> 用这个本地 SOCKS5 实际请求一个轻量的连通性检测 URL
-> 记录延迟 -> 关闭进程。 对一批节点做并发验证,控制并发度避免机器上开太多子进程。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass

import httpx

from .local_proxy import RunningProxy, start_local_proxy
from .subscription import ProxyNode

DEFAULT_CHECK_URL = "https://www.gstatic.com/generate_204"
# start_local_proxy / external_engine.start_local_proxy 在"这个节点纯 Python 处理不了
# 且本机没有可用外部引擎"时,抛出的 RuntimeError 里都带这个关键词,用来在统计时
# 区分"协议/特性不支持被跳过"和"协议支持但实际连不上"这两种情况。
UNSUPPORTED_MARKER = "需要外部引擎"


@dataclass
class ValidationResult:
    node: ProxyNode
    ok: bool
    latency_ms: float | None
    error: str | None = None


async def validate_node(
    node: ProxyNode, check_url: str = DEFAULT_CHECK_URL, timeout: float = 8.0
) -> ValidationResult:
    running = None
    try:
        running = await start_local_proxy(node)
        start = time.monotonic()
        async with httpx.AsyncClient(proxy=running.socks_url, timeout=timeout) as client:
            resp = await client.get(check_url)
            latency = (time.monotonic() - start) * 1000
            ok = resp.status_code in (200, 204)
            return ValidationResult(node=node, ok=ok, latency_ms=latency if ok else None)
    except Exception as e:  # noqa: BLE001
        return ValidationResult(node=node, ok=False, latency_ms=None, error=str(e))
    finally:
        if running is not None:
            await running.stop()


async def validate_nodes(
    nodes: list[ProxyNode], concurrency: int = 8, check_url: str = DEFAULT_CHECK_URL
) -> list[ValidationResult]:
    sem = asyncio.Semaphore(concurrency)

    async def _run(n: ProxyNode) -> ValidationResult:
        async with sem:
            return await validate_node(n, check_url=check_url)

    return await asyncio.gather(*[_run(n) for n in nodes])
