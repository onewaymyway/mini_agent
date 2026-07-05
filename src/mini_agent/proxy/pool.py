"""代理池:统一管理"可用代理"的生命周期,供上层按不同策略取用。

两种取用场景:
- get_best_socks_url(): 给"主 LLM 请求"用,尽量固定用延迟最低、最稳定的一个,
  避免频繁切换导致的连接抖动。
- get_rotating_socks_url(): 给"抓取/爬虫类工具"用,每次调用换一个节点,
  配合上层的重试逻辑做反封锁轮换。

代理池自己不长期占用一堆 xray 子进程——同一时间只保持"当前在用的" N 个
本地端口常驻,其余节点只在验证阶段短暂拉起。这样可以避免节点数量一多就
把机器上的子进程/端口耗尽。
"""

from __future__ import annotations

import asyncio
import itertools
import time
from dataclasses import dataclass, field

from .local_proxy import RunningProxy, start_local_proxy
from .subscription import ProxyNode, SubscriptionSource, fetch_all
from .validator import ValidationResult, validate_nodes


@dataclass
class PoolEntry:
    node: ProxyNode
    latency_ms: float
    last_checked: float
    running: RunningProxy | None = None  # 只有被"激活"的节点才会有常驻进程


class ProxyPool:
    def __init__(
        self,
        sources: list[SubscriptionSource],
        keep_alive_count: int = 3,
        refresh_interval_sec: int = 1800,
    ):
        self.sources = sources
        self.keep_alive_count = keep_alive_count
        self.refresh_interval_sec = refresh_interval_sec
        self._entries: dict[str, PoolEntry] = {}
        self._rotation = itertools.cycle([])
        self._lock = asyncio.Lock()
        self._refresh_task: asyncio.Task | None = None

    async def refresh(self) -> list[ValidationResult]:
        """抓取所有订阅源 -> 验证 -> 更新可用节点排名 -> 激活 top-N 常驻。"""
        async with self._lock:
            nodes = await fetch_all(self.sources)
            if not nodes:
                return []
            results = await validate_nodes(nodes)
            ok_results = sorted(
                (r for r in results if r.ok and r.latency_ms is not None),
                key=lambda r: r.latency_ms,
            )

            # 关掉旧的常驻进程
            for entry in self._entries.values():
                if entry.running:
                    await entry.running.stop()
            self._entries.clear()

            now = time.time()
            top = ok_results[: self.keep_alive_count]
            for r in top:
                running = await start_local_proxy(r.node)
                self._entries[r.node.key()] = PoolEntry(
                    node=r.node, latency_ms=r.latency_ms, last_checked=now, running=running
                )
            self._rotation = itertools.cycle(list(self._entries.keys())) if self._entries else itertools.cycle([])
            return results

    async def start_auto_refresh(self) -> None:
        if self._refresh_task is not None:
            return

        async def _loop():
            while True:
                try:
                    await self.refresh()
                except Exception:
                    pass
                await asyncio.sleep(self.refresh_interval_sec)

        self._refresh_task = asyncio.create_task(_loop())

    async def stop(self) -> None:
        if self._refresh_task:
            self._refresh_task.cancel()
        async with self._lock:
            for entry in self._entries.values():
                if entry.running:
                    await entry.running.stop()
            self._entries.clear()

    def get_best_socks_url(self) -> str | None:
        """给主 LLM 请求用: 延迟最低的一个。"""
        if not self._entries:
            return None
        best = min(self._entries.values(), key=lambda e: e.latency_ms)
        return best.running.socks_url if best.running else None

    def get_rotating_socks_url(self) -> str | None:
        """给抓取类工具用: 轮换。"""
        if not self._entries:
            return None
        try:
            key = next(self._rotation)
        except StopIteration:
            return None
        entry = self._entries.get(key)
        return entry.running.socks_url if entry and entry.running else None

    def status(self) -> list[dict]:
        return [
            {"node": e.node.name, "key": key, "latency_ms": round(e.latency_ms, 1)}
            for key, e in self._entries.items()
        ]
