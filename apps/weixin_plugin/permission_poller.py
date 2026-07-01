"""
permission_poller.py
======================
后台轮询各微信用户在 mini_agent 里的待审批请求（/v1/permissions/pending），
发现新请求时主动推送一条微信消息，并把 req_id 记到
MiniAgentHandler.pending_permission_by_openid，供用户回复
/yes /no /always /denyalways 时使用。

一期用轮询（简单、稳，缺点是有 3~5 秒延迟）；SSE 常驻订阅作为二期优化。
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from weixin.bot import WeixinBot
    from weixin.handlers.mini_agent_handler import MiniAgentHandler

logger = logging.getLogger(__name__)

PERMISSION_TIMEOUT_REMIND_S = 600  # 待审批请求超过 10 分钟未处理，提醒一次


def _summarize_permission(perm: dict) -> str:
    tool = perm.get("tool_name") or perm.get("tool") or "未知操作"
    raw_input = perm.get("input") or perm.get("tool_input") or {}
    summary = str(raw_input)
    if len(summary) > 200:
        summary = summary[:200] + "…"
    return f"⚠️ Agent 请求执行：{tool}\n参数：{summary}\n\n回复 /yes 允许一次 / /no 拒绝一次 / /always 以后同类自动允许 / /denyalways 以后同类自动拒绝"


class PermissionPoller:
    """按用户轮询待审批请求并推送微信消息。"""

    def __init__(
        self,
        bot: "WeixinBot",
        handler: "MiniAgentHandler",
        poll_interval_s: float = 4.0,
    ) -> None:
        self.bot = bot
        self.handler = handler
        self.poll_interval_s = poll_interval_s
        # req_id -> 首次发现时间戳，用于超时提醒判断
        self._first_seen_at: dict[str, float] = {}
        # req_id -> 是否已经提醒过一次
        self._reminded: set[str] = set()
        self._running = False

    async def run(self) -> None:
        self._running = True
        logger.info("PermissionPoller 已启动，轮询间隔 %.1fs", self.poll_interval_s)
        while self._running:
            try:
                await self._poll_once()
            except asyncio.CancelledError:
                break
            except Exception:
                logger.exception("PermissionPoller 轮询出错")
            await asyncio.sleep(self.poll_interval_s)

    def stop(self) -> None:
        self._running = False

    async def _poll_once(self) -> None:
        store = self.handler.store
        client = self.handler.client

        # 遍历所有已知的微信用户（已建立映射的），逐个查待审批列表。
        # 用户量大时这里会有 N 次 API 调用；如果之后成为瓶颈，可以考虑
        # mini_agent 侧加一个"跨用户聚合"的端点，本次先用最简单的方式。
        cur = store._conn.execute("SELECT openid, token FROM user_mapping")
        rows = cur.fetchall()

        for openid, token in rows:
            try:
                pending = await client.list_pending_permissions(token=token)
            except Exception:
                logger.exception("查询 openid=%s 的待审批列表失败", openid)
                continue

            if not pending:
                continue

            # 同一用户同时只关注最新一条，避免指令响应对象产生歧义
            latest = pending[-1]
            req_id = latest.get("req_id") or latest.get("id")
            if not req_id:
                continue

            already_tracked = self.handler.pending_permission_by_openid.get(openid) == req_id
            now = time.monotonic()

            if not already_tracked:
                self.handler.pending_permission_by_openid[openid] = req_id
                self._first_seen_at[req_id] = now
                self._reminded.discard(req_id)
                await self.bot.send_text(openid, _summarize_permission(latest))
                continue

            # 已经推送过，检查是否需要超时提醒
            first_seen = self._first_seen_at.get(req_id, now)
            if req_id not in self._reminded and (now - first_seen) >= PERMISSION_TIMEOUT_REMIND_S:
                self._reminded.add(req_id)
                await self.bot.send_text(
                    openid,
                    "⏰ 提醒：你还有一条待审批请求未处理，回复 /yes /no /always /denyalways 处理它。",
                )
