"""
weixin.handlers.mini_agent_handler
====================================
把微信消息路由到本项目 mini_agent 的 HTTP API。

支持的能力（见 apps/weixin_plugin/../../ 设计文档）：
  - 普通文本 → 当前 session 的 /v1/chat，轮询取回复
  - /help /status /interrupt
  - /sessions /session new /session use <序号|id> /session del <序号|id>
  - /ls /cat /find（只读文件查看）
  - /yes /no /always /denyalways（响应最近一条待审批请求，实际推送逻辑
    在 permission_poller.py 里，这里只处理"用户回复确认指令"这一半）

不在本文件里做的事：
  - 权限请求的主动推送（由 PermissionPoller 负责，见 permission_poller.py）
  - 自然语言指令路由（先只做斜杠指令，见设计文档"二期优化"）
"""

from __future__ import annotations

import logging
from typing import Optional

from ..bot import BaseHandler, WeixinBot
from ..types import WeixinMessage
from mini_agent_client import MiniAgentClient, MiniAgentAPIError, TurnResult
from user_mapping import RoleRules, UserMappingStore, get_or_create_user

logger = logging.getLogger(__name__)

HELP_TEXT = (
    "📖 可用指令：\n"
    "直接发文字 — 与当前会话对话\n"
    "/sessions — 查看我的所有会话\n"
    "/session new — 新建会话并切换\n"
    "/session use <序号> — 切换到指定会话\n"
    "/session del <序号> — 删除指定会话\n"
    "/status — 查看当前状态\n"
    "/interrupt — 中断当前任务\n"
    "/ls [路径] — 查看目录\n"
    "/cat <路径> — 查看文件内容\n"
    "/find <关键词> — 搜索文件\n"
    "/yes /no /always /denyalways — 响应待审批请求\n"
    "/help — 查看本帮助"
)

MAX_REPLY_CHARS = 1800  # 微信单条消息长度上限的保守值
MAX_FILE_CHARS = 1500


def _truncate(text: str, limit: int, suffix: str = "\n…（内容过长，已截断）") -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + suffix


class MiniAgentHandler(BaseHandler):
    """把微信文本消息路由到 mini_agent /v1/* API 的 Handler。"""

    def __init__(
        self,
        mini_agent_base_url: str,
        owner_token: str,
        role_rules: RoleRules,
        db_path: str = "apps/weixin_plugin/data/user_mapping.db",
        poll_interval_s: float = 1.5,
        chat_timeout_s: float = 180.0,
    ) -> None:
        self.client = MiniAgentClient(base_url=mini_agent_base_url)
        self.owner_token = owner_token
        self.role_rules = role_rules
        self.store = UserMappingStore(db_path)
        self.poll_interval_s = poll_interval_s
        self.chat_timeout_s = chat_timeout_s
        # openid -> 最近一次待用户响应的权限请求 req_id（由 PermissionPoller 写入）
        self.pending_permission_by_openid: dict[str, str] = {}

    # ------------------------------------------------------------------
    # BaseHandler 接口
    # ------------------------------------------------------------------

    async def on_text(self, bot: WeixinBot, msg: WeixinMessage, text: str) -> None:
        openid = msg.from_user_id
        if not openid:
            return
        text = text.strip()

        try:
            rec = await get_or_create_user(
                self.store, self.client, self.owner_token, openid, self.role_rules
            )
        except MiniAgentAPIError as e:
            await bot.reply_text(msg, f"❌ 初始化用户失败：{e}")
            return

        try:
            if text.startswith("/"):
                await self._handle_command(bot, msg, openid, rec.token, text)
            else:
                await self._handle_chat(bot, msg, rec.token, text)
        except MiniAgentAPIError as e:
            await bot.reply_text(msg, f"❌ mini_agent 请求失败：{e}")
        except Exception:
            logger.exception("MiniAgentHandler 处理消息出错")
            await bot.reply_text(msg, "❌ 内部错误，请稍后重试")

    # ------------------------------------------------------------------
    # 对话
    # ------------------------------------------------------------------

    async def _handle_chat(self, bot: WeixinBot, msg: WeixinMessage, token: str, text: str) -> None:
        turn_id, _session_id = await self.client.chat(token=token, message=text)
        result: TurnResult = await self.client.wait_turn_result(
            token=token,
            turn_id=turn_id,
            poll_interval_s=self.poll_interval_s,
            timeout_s=self.chat_timeout_s,
        )

        if result.timed_out:
            await bot.reply_text(msg, "⏳ 任务仍在执行，回复较慢，可稍后发送 /status 查看进度。")
            return
        if result.state == "error":
            await bot.reply_text(msg, "❌ 执行出错，请发送 /status 查看详情或重试。")
            return
        if result.state == "interrupted":
            await bot.reply_text(msg, "⏹️ 任务已被中断。")
            return

        reply = result.text or "（无回复内容）"
        await bot.reply_text(msg, _truncate(reply, MAX_REPLY_CHARS))

    # ------------------------------------------------------------------
    # 指令路由
    # ------------------------------------------------------------------

    async def _handle_command(
        self, bot: WeixinBot, msg: WeixinMessage, openid: str, token: str, text: str
    ) -> None:
        parts = text.split(maxsplit=2)
        cmd = parts[0].lower()

        if cmd == "/help":
            await bot.reply_text(msg, HELP_TEXT)

        elif cmd == "/status":
            await self._cmd_status(bot, msg, token)

        elif cmd == "/interrupt":
            ok = await self.client.interrupt(token=token)
            await bot.reply_text(msg, "⏹️ 已请求中断" if ok else "❌ 中断失败")

        elif cmd == "/sessions":
            await self._cmd_list_sessions(bot, msg, openid, token)

        elif cmd == "/session":
            await self._cmd_session(bot, msg, openid, token, parts[1:])

        elif cmd == "/ls":
            await self._cmd_ls(bot, msg, token, parts[1] if len(parts) > 1 else "")

        elif cmd == "/cat":
            if len(parts) < 2:
                await bot.reply_text(msg, "用法：/cat <路径>")
            else:
                await self._cmd_cat(bot, msg, token, parts[1])

        elif cmd == "/find":
            if len(parts) < 2:
                await bot.reply_text(msg, "用法：/find <关键词>")
            else:
                await self._cmd_find(bot, msg, token, " ".join(parts[1:]))

        elif cmd in ("/yes", "/no", "/always", "/denyalways"):
            await self._cmd_permission_reply(bot, msg, openid, token, cmd)

        else:
            await bot.reply_text(msg, f"未知指令：{cmd}\n\n{HELP_TEXT}")

    async def _cmd_status(self, bot: WeixinBot, msg: WeixinMessage, token: str) -> None:
        status = await self.client.get_status(token=token)
        lines = [
            f"状态：{status.get('state')}",
            f"当前 turn：{status.get('turn_id') or '无'}",
            f"当前 session：{status.get('session_id') or '无'}",
            f"队列长度：{status.get('queue_depth', 0)}",
        ]
        await bot.reply_text(msg, "\n".join(lines))

    # ------------------------------------------------------------------
    # Session 管理
    # ------------------------------------------------------------------

    async def _cmd_list_sessions(self, bot: WeixinBot, msg: WeixinMessage, openid: str, token: str) -> None:
        resp = await self.client.list_sessions(token=token)
        sessions = resp.get("sessions", [])
        current_id = resp.get("current_session_id")

        if not sessions:
            await bot.reply_text(msg, "暂无会话，发送 /session new 创建一个。")
            return

        self.store.save_session_index(openid, [s["id"] for s in sessions])

        lines = ["📂 我的会话："]
        for i, s in enumerate(sessions, start=1):
            mark = "●" if s["id"] == current_id else " "
            title = s.get("title") or "(未命名)"
            lines.append(f"{mark}{i}. {title}（{s.get('age', '')}，{s.get('turns', 0)}轮）")
        lines.append("\n发送 /session use <序号> 切换")
        await bot.reply_text(msg, "\n".join(lines))

    async def _cmd_session(
        self, bot: WeixinBot, msg: WeixinMessage, openid: str, token: str, args: list[str]
    ) -> None:
        if not args:
            await bot.reply_text(msg, "用法：/session new | /session use <序号> | /session del <序号>")
            return

        sub = args[0].lower()

        if sub == "new":
            resp = await self.client.new_session(token=token)
            await bot.reply_text(msg, f"✅ 已新建会话（{resp.get('session_id')}）")
            return

        if sub in ("use", "del", "delete") and len(args) >= 2:
            ref = args[1]
            session_id = self.store.resolve_session_ref(openid, ref)
            if not session_id:
                await bot.reply_text(msg, f"找不到序号 {ref}，请先发送 /sessions 查看列表")
                return

            if sub == "use":
                resp = await self.client.resume_session(token=token, session_id=session_id)
                await bot.reply_text(msg, f"✅ 已切换到会话 {resp.get('session_id')}")
            else:
                await self.client.delete_session(token=token, session_id=session_id)
                await bot.reply_text(msg, f"🗑️ 已删除会话 {session_id}")
            return

        await bot.reply_text(msg, "用法：/session new | /session use <序号> | /session del <序号>")

    # ------------------------------------------------------------------
    # 文件查看（只读）
    # ------------------------------------------------------------------

    async def _cmd_ls(self, bot: WeixinBot, msg: WeixinMessage, token: str, path: str) -> None:
        resp = await self.client.fs_list(token=token, path=path)
        entries = resp.get("entries", []) if resp else []
        if not entries:
            await bot.reply_text(msg, f"📁 {path or '/'}（空目录或不存在）")
            return

        lines = [f"📁 {path or '/'}"]
        for e in entries[:100]:
            icon = "📁" if e.get("is_dir") else "📄"
            lines.append(f"{icon} {e.get('name')}")
        if len(entries) > 100:
            lines.append(f"…还有 {len(entries) - 100} 项，未全部显示")
        await bot.reply_text(msg, _truncate("\n".join(lines), MAX_REPLY_CHARS))

    async def _cmd_cat(self, bot: WeixinBot, msg: WeixinMessage, token: str, path: str) -> None:
        resp = await self.client.fs_read(token=token, path=path)
        content = resp.get("content", "") if resp else ""
        if not content:
            await bot.reply_text(msg, f"📄 {path}（空文件或不存在）")
            return
        await bot.reply_text(msg, f"📄 {path}\n" + _truncate(content, MAX_FILE_CHARS))

    async def _cmd_find(self, bot: WeixinBot, msg: WeixinMessage, token: str, query: str) -> None:
        resp = await self.client.fs_search(token=token, query=query)
        results = resp.get("results", []) if resp else []
        if not results:
            await bot.reply_text(msg, f"🔍 未找到匹配 “{query}” 的文件")
            return
        lines = [f"🔍 匹配 “{query}”："]
        for r in results[:50]:
            if isinstance(r, dict):
                icon = "📁" if r.get("is_dir") else "📄"
                lines.append(f"{icon} {r.get('path', r.get('name', r))}")
            else:
                lines.append(str(r))
        await bot.reply_text(msg, _truncate("\n".join(lines), MAX_REPLY_CHARS))

    # ------------------------------------------------------------------
    # 权限审批响应（配合 permission_poller.py）
    # ------------------------------------------------------------------

    async def _cmd_permission_reply(
        self, bot: WeixinBot, msg: WeixinMessage, openid: str, token: str, cmd: str
    ) -> None:
        req_id = self.pending_permission_by_openid.get(openid)
        if not req_id:
            await bot.reply_text(msg, "当前没有待响应的审批请求。")
            return

        approve = cmd in ("/yes", "/always")
        mode = {
            "/yes": "once",
            "/no": "once",
            "/always": "always",
            "/denyalways": "deny_always",
        }[cmd]

        ok = await self.client.respond_permission(token=token, req_id=req_id, approve=approve, mode=mode)
        self.pending_permission_by_openid.pop(openid, None)
        await bot.reply_text(msg, "✅ 已提交你的决定" if ok else "❌ 提交失败，请重试")
