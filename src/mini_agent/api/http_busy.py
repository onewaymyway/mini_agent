"""
api/http_busy.py — HTTP 服务 in-flight 请求忙碌度计数

背景（next_doc/daemon_dual_signal_hang_detection_plan.md 阶段 C）：阶段 B 已经
把"核心调度是否卡死"这件事拆成了独立的磁盘旁路信号（见
evolution/scheduler_heartbeat.py + cli/daemon.py 的 `read_scheduler_heartbeat_
status()`），不再需要靠 HTTP 层响应快慢去猜。但看板"🧠 自我状态"里目前仍然
把"HTTP 服务本身忙不忙"和"核心调度心跳"混在一段文字里展示，用户区分不出
"daemon 只是这一刻请求有点多、很快会消化完"和"核心调度已经真的卡死"这两种
截然不同的情况。

本模块提供一个极轻量的进程内 in-flight 请求计数器 + 一个 ASGI 中间件：
请求进入时计数 +1、离开时 -1，同时记录"当前仍未完成、且开始时间最早"的那个
请求已经挂了多久。不需要额外线程，`threading.Lock` 保护的几个整数/浮点数
读写，开销可以忽略不计——与 http_log.py 的访问日志中间件是同一量级的成本，
但这里连日志文件 IO 都没有，只是内存计数。

与 evolution/scheduler_heartbeat.py 磁盘旁路的关系：两者是并列的、互不依赖
的两路观测信号——`HttpBusyTracker` 反映的是"HTTP 层此刻有多少请求在排队/
处理"，只在 event loop 还能正常调度时才有意义（event loop 卡死时这个计数
器本身也读不出来，跟 `/v1/health` 是同一条命）；`scheduler_heartbeat` 磁盘
旁路反映的是"核心调度是否卡在某次 tick() 里"，不经过 event loop。阶段 B
的判定矩阵已经用后者做卡死判定的主信号，本模块只是给看板一个更细粒度的
"HTTP 忙不忙"展示维度，不参与 supervisor 的卡死判定逻辑。
"""

from __future__ import annotations

import threading
import time
from typing import Optional


class HttpBusyTracker:
    """进程内 in-flight HTTP 请求计数器，线程安全。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._in_flight_count = 0
        # 按 id(scope) 记录每个仍在处理中的请求的开始时间（单调时钟），
        # 用于算出"最早开始、至今仍未完成"的那个请求已经挂了多久——
        # 比只看 in_flight_count 更能说明问题：3 个请求同时在跑，但都
        # 是刚进来的，和 3 个请求里有一个已经卡了 12 秒，是完全不同的
        # 严重程度。
        self._started_at: dict[int, float] = {}

    def request_started(self, token: int) -> None:
        with self._lock:
            self._in_flight_count += 1
            self._started_at[token] = time.monotonic()

    def request_finished(self, token: int) -> None:
        with self._lock:
            self._in_flight_count = max(0, self._in_flight_count - 1)
            self._started_at.pop(token, None)

    def snapshot(self) -> dict:
        """返回当前忙碌度快照，供 `/v1/self/execution_model_status` 汇总展示。"""
        with self._lock:
            count = self._in_flight_count
            oldest_started_at = min(self._started_at.values()) if self._started_at else None
        oldest_in_flight_seconds = (
            max(0.0, time.monotonic() - oldest_started_at)
            if oldest_started_at is not None
            else 0.0
        )
        return {
            "in_flight_count": count,
            "oldest_in_flight_seconds": round(oldest_in_flight_seconds, 3),
        }


class HttpBusyMiddleware:
    """纯 ASGI 中间件（不用 BaseHTTPMiddleware，避免多一层 Starlette 的
    request/response 包装开销——这里只关心 http 类型的请求，其它 scope
    类型（如 websocket/lifespan）直接透传，不计数）。

    放在哪一层顺序都不影响正确性（不做鉴权/日志），因此不强求它是最内层
    还是最外层；`api/server.py` 里把它加在其它中间件之后即可。
    """

    def __init__(self, app, tracker: Optional[HttpBusyTracker] = None) -> None:
        self._app = app
        self.tracker = tracker if tracker is not None else HttpBusyTracker()

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self._app(scope, receive, send)
            return

        token = id(scope)
        self.tracker.request_started(token)
        try:
            await self._app(scope, receive, send)
        finally:
            self.tracker.request_finished(token)
