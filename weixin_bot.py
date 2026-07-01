#!/usr/bin/env python3
"""
weixin_bot.py — 微信端接入（直接内嵌模式）
============================================
与 main.py 同级，放在项目根目录，这样 mini_agent 的包结构、
agent_config.json、providers.json、skills/ 等全部自动复用。

运行：
    python weixin_bot.py [--project <路径>] [--yes] [--no-stream]

    --project / -p   指定项目根目录（默认为脚本所在目录，即本目录）
    --yes / -y       自动批准所有工具调用（危险，谨慎使用）

配置全部走项目根目录的 agent_config.json + providers.json，
与 python main.py 完全一致，无需额外配置文件。

微信网关配置（openclaw）通过环境变量读取：
    WEIXIN_BASE_URL  — openclaw 网关地址（默认走 ~/.openclaw/openclaw.json）
    WEIXIN_TOKEN     — 网关 token（默认走 ~/.openclaw/openclaw.json）

架构：
    WeixinBot (asyncio 事件循环)
      └── WeixinHandler
            ├── WeixinPermissionGuard  # 覆盖 _prompt()，把终端等待改为微信消息+Event
            └── dict[openid → Agent]  # 每个微信用户独立 Agent 实例，上下文隔离

权限审批：
    Agent 执行危险工具前会向微信用户推一条消息，
    用户回复 /yes /no /always /denyalways 即可，
    无需坐在终端旁边。
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


# ── 将 src/ 加入 Python 路径，与 main.py 保持一致 ───────────────────────────
_HERE = Path(__file__).parent
sys.path.insert(0, str(_HERE / "src"))

# ── mini_agent 导入 ───────────────────────────────────────────────────────────
from mini_agent.config import load_config
from mini_agent.agent import Agent
from mini_agent.permissions import PermissionGuard
from mini_agent.skills import SkillLoader

# ── weixin SDK 导入 ───────────────────────────────────────────────────────────
sys.path.insert(0, str(_HERE / "apps" / "weixin_plugin"))
from weixin import WeixinBot, auto_token
from weixin.bot import BaseHandler
from weixin.types import WeixinMessage
from weixin.login import load_or_login

logger = logging.getLogger(__name__)

MAX_REPLY_CHARS = 1800   # 微信单条消息字符上限（保守值）
MAX_FILE_CHARS  = 1500
PERM_TIMEOUT_S  = 300.0  # 权限审批等待超时（秒），超时自动拒绝
THREAD_WORKERS  = 4      # 同时处理几个用户对话的线程数


def _truncate(text: str, limit: int, suffix: str = "\n…（内容过长，已截断）") -> str:
    return text if len(text) <= limit else text[:limit] + suffix


# ── 权限审批：把终端阻塞改成微信消息 + threading.Event ───────────────────────

@dataclass
class WeixinPermissionGuard(PermissionGuard):
    """
    覆盖 PermissionGuard._prompt()。
    原版 _prompt() 阻塞在终端等用户输入；
    这里改成：
      1. 调 _push_fn(text) 向微信用户推一条审批消息
      2. 阻塞在 threading.Event 上，等用户回复 /yes /no /always /denyalways
      3. 超时 PERM_TIMEOUT_S 秒自动拒绝

    _push_fn / _pending_event / _pending_result 由 WeixinHandler 在
    每次 run_turn 之前注入，确保推消息的目标 openid 是正确的。
    """

    _push_fn: Optional[callable] = field(default=None, init=False, repr=False)
    _pending_event: Optional[threading.Event] = field(default=None, init=False, repr=False)
    _pending_result: Optional[list] = field(default=None, init=False, repr=False)  # [bool, str]

    def _prompt(self, tool_name: str, tool_input: dict, is_dangerous: bool) -> bool:
        if self._push_fn is None or self._pending_event is None:
            logger.warning("WeixinPermissionGuard: _push_fn 未注入，默认拒绝 %s", tool_name)
            return False

        from mini_agent.permissions import _summarise
        summary = _summarise(tool_name, tool_input)
        tag = "⚠️ 危险操作" if is_dangerous else "🔧 工具请求"
        msg = (
            f"{tag}：{tool_name}\n"
            f"参数：{summary[:300]}\n\n"
            "回复：\n"
            "/yes — 允许一次\n"
            "/always — 以后同类自动允许\n"
            "/no — 拒绝一次\n"
            "/denyalways — 以后同类自动拒绝"
        )

        self._pending_event.clear()
        self._pending_result.clear()
        self._push_fn(msg)

        responded = self._pending_event.wait(timeout=PERM_TIMEOUT_S)
        if not responded:
            self._push_fn("⏰ 审批超时，已自动拒绝。")
            return False

        approved = self._pending_result[0]
        mode = self._pending_result[1] if len(self._pending_result) > 1 else "once"

        if approved and mode == "always":
            self._add_allow(tool_name, tool_input)
        elif not approved and mode == "deny_always":
            self._denied_tools.add(tool_name)
            self._save_permissions()

        return approved

    def resolve_reply(self, cmd: str) -> bool:
        """
        用户发了 /yes /no /always /denyalways，解除 _prompt 阻塞。
        返回 True 表示有待响应的请求；False 表示当前没有。
        """
        if self._pending_event is None or self._pending_event.is_set():
            return False
        approve = cmd in ("/yes", "/always")
        mode = {
            "/yes": "once", "/no": "once",
            "/always": "always", "/denyalways": "deny_always",
        }.get(cmd, "once")
        self._pending_result[:] = [approve, mode]
        self._pending_event.set()
        return True


# ── 每个微信用户的 Agent 上下文 ───────────────────────────────────────────────

@dataclass
class _UserCtx:
    agent: Agent
    guard: WeixinPermissionGuard
    session_index: dict = field(default_factory=dict)  # "1" / "2" → session_id
    busy: bool = False


# ── 核心 Handler ──────────────────────────────────────────────────────────────

HELP_TEXT = """📖 可用指令：
直接发文字 — 与 Agent 对话
/sessions — 列出我的所有会话
/session new — 新建会话
/session use <序号> — 切换会话
/session del <序号> — 删除会话
/status — 当前 Agent 状态
/ls [路径] — 查看目录
/cat <路径> — 查看文件（只读）
/find <关键词> — 搜索文件名
/yes /no /always /denyalways — 响应审批请求
/help — 查看本帮助"""


class WeixinHandler(BaseHandler):
    """
    直接内嵌 mini_agent 的微信 Handler。
    每个微信 openid 对应一个独立的 Agent 实例，上下文完全隔离。
    Agent.run_turn() 在 ThreadPoolExecutor 里运行（Agent 是同步的）。
    """

    def __init__(self, project_root: Path, auto_approve: bool = False) -> None:
        self._project_root = project_root
        self._auto_approve = auto_approve
        self._executor = ThreadPoolExecutor(
            max_workers=THREAD_WORKERS, thread_name_prefix="weixin-agent"
        )
        self._contexts: dict[str, _UserCtx] = {}

    # ── BaseHandler 接口 ─────────────────────────────────────────────────────

    async def on_text(self, bot: WeixinBot, msg: WeixinMessage, text: str) -> None:
        openid = msg.from_user_id
        if not openid:
            return
        text = text.strip()
        ctx = self._get_or_create(openid)

        try:
            if text.startswith("/"):
                await self._dispatch_command(bot, msg, openid, ctx, text)
            else:
                await self._do_chat(bot, msg, openid, ctx, text)
        except Exception:
            logger.exception("WeixinHandler 出错 openid=%s text=%r", openid, text)
            await bot.reply_text(msg, "❌ 内部错误，请稍后重试")

    # ── Agent 实例化（与 main.py 相同的配置加载方式） ───────────────────────

    def _get_or_create(self, openid: str) -> _UserCtx:
        if openid not in self._contexts:
            self._contexts[openid] = self._make_ctx()
        return self._contexts[openid]

    def _make_ctx(self) -> _UserCtx:
        """
        与 cli/app.py 完全相同的配置加载 + Agent 初始化方式：
          load_config(project_root=...)  自动读 agent_config.json + providers.json
        只额外关掉两个终端相关选项：stream=False、verbose=False。
        """
        cfg = load_config(
            project_root=self._project_root,
            auto_approve=self._auto_approve,
            # 关掉终端流式打印和 verbose（微信里没有终端）
            verbose=False,
            stream=False,
        )
        cfg.stream = False  # 确保关闭（load_config 可能被 agent_config.json 覆盖）

        skill_dirs: list[Path] = [cfg.skills_dir] if cfg.skills_dir else []
        skill_loader = SkillLoader(
            skill_dirs,
            per_skill_tokens=getattr(cfg, "skill_compact_per_skill", 5_000),
            total_budget=getattr(cfg, "skill_compact_budget", 25_000),
        )

        guard = WeixinPermissionGuard(
            auto_approve=self._auto_approve,
            sandbox=cfg.sandbox,
            project_root=self._project_root,
        )
        guard._pending_event = threading.Event()
        guard._pending_result = []

        agent = Agent(cfg=cfg, skill_loader=skill_loader, guard=guard)
        return _UserCtx(agent=agent, guard=guard)

    # ── 对话 ─────────────────────────────────────────────────────────────────

    async def _do_chat(
        self, bot: WeixinBot, msg: WeixinMessage,
        openid: str, ctx: _UserCtx, text: str,
    ) -> None:
        if ctx.busy:
            await bot.reply_text(msg, "⏳ 上一条消息还在处理中，请稍候…")
            return

        ctx.busy = True
        loop = asyncio.get_event_loop()

        def _push_sync(weixin_text: str) -> None:
            """从 Agent 工作线程安全地往微信推消息。"""
            asyncio.run_coroutine_threadsafe(
                bot.send_text(openid, weixin_text), loop
            ).result(timeout=10)

        def _run() -> str:
            ctx.guard._push_fn = _push_sync
            try:
                return ctx.agent.run_turn(text)
            finally:
                ctx.guard._push_fn = None
                ctx.busy = False

        try:
            result = await loop.run_in_executor(self._executor, _run)
        except Exception as exc:
            ctx.busy = False
            logger.exception("run_turn 失败 openid=%s", openid)
            await bot.reply_text(msg, f"❌ 执行出错：{exc}")
            return

        reply = (result or "").strip() or "（无回复内容）"
        await bot.reply_text(msg, _truncate(reply, MAX_REPLY_CHARS))

    # ── 指令分发 ─────────────────────────────────────────────────────────────

    async def _dispatch_command(
        self, bot: WeixinBot, msg: WeixinMessage,
        openid: str, ctx: _UserCtx, text: str,
    ) -> None:
        parts = text.split(maxsplit=2)
        cmd = parts[0].lower()

        if cmd == "/help":
            await bot.reply_text(msg, HELP_TEXT)

        elif cmd == "/status":
            await bot.reply_text(msg, self._status_text(ctx))

        elif cmd == "/sessions":
            await self._cmd_list_sessions(bot, msg, openid, ctx)

        elif cmd == "/session":
            await self._cmd_session(bot, msg, openid, ctx, parts[1:])

        elif cmd == "/ls":
            await self._cmd_ls(bot, msg, ctx, parts[1] if len(parts) > 1 else "")

        elif cmd == "/cat":
            if len(parts) < 2:
                await bot.reply_text(msg, "用法：/cat <路径>")
            else:
                await self._cmd_cat(bot, msg, ctx, parts[1])

        elif cmd == "/find":
            if len(parts) < 2:
                await bot.reply_text(msg, "用法：/find <关键词>")
            else:
                await self._cmd_find(bot, msg, ctx, " ".join(parts[1:]))

        elif cmd in ("/yes", "/no", "/always", "/denyalways"):
            ok = ctx.guard.resolve_reply(cmd)
            if not ok:
                await bot.reply_text(msg, "当前没有待响应的审批请求。")

        else:
            await bot.reply_text(msg, f"未知指令：{cmd}\n\n{HELP_TEXT}")

    # ── /status ──────────────────────────────────────────────────────────────

    def _status_text(self, ctx: _UserCtx) -> str:
        a = ctx.agent
        lines = [
            f"状态：{'执行中 🔄' if ctx.busy else '空闲 ✅'}",
            f"当前会话：{a.session_id or '无'}",
            f"历史轮次：{a.stats.turns}",
            f"Input tokens：{a.stats.input_tokens:,}",
            f"Output tokens：{a.stats.output_tokens:,}",
        ]
        return "\n".join(lines)

    # ── /sessions ─────────────────────────────────────────────────────────────

    async def _cmd_list_sessions(
        self, bot: WeixinBot, msg: WeixinMessage, openid: str, ctx: _UserCtx
    ) -> None:
        mgr = ctx.agent.session_manager
        if not mgr:
            await bot.reply_text(msg, "Session 功能未启用（agent_config.json 中 auto_save_session 为 false？）")
            return

        sessions = mgr.list_sessions()
        if not sessions:
            await bot.reply_text(msg, "暂无会话，发送 /session new 创建一个。")
            return

        current = ctx.agent.session_id
        ctx.session_index.clear()
        lines = ["📂 我的会话："]
        for i, s in enumerate(sessions, start=1):
            ctx.session_index[str(i)] = s.id
            mark = "●" if s.id == current else " "
            title = (s.title or s.id[:8]).strip()
            lines.append(f"{mark} {i}. {title}（{s.turns} 轮，{s.age_str}）")
        lines.append("\n/session use <序号> 切换  /session del <序号> 删除")
        await bot.reply_text(msg, "\n".join(lines))

    # ── /session new|use|del ──────────────────────────────────────────────────

    async def _cmd_session(
        self, bot: WeixinBot, msg: WeixinMessage,
        openid: str, ctx: _UserCtx, args: list[str],
    ) -> None:
        if not args:
            await bot.reply_text(msg, "用法：/session new | use <序号> | del <序号>")
            return

        sub = args[0].lower()

        if sub == "new":
            ok = ctx.agent.new_session()
            if ok:
                await bot.reply_text(msg, f"✅ 已新建会话（{ctx.agent.session_id}）")
            else:
                await bot.reply_text(msg, "❌ 新建会话失败")
            return

        if sub in ("use", "del", "delete") and len(args) >= 2:
            ref = args[1].strip()
            # 支持序号（上次 /sessions 的编号）或直接 session id
            session_id = ctx.session_index.get(ref, ref)

            if sub == "use":
                ok = ctx.agent.load_session(session_id)
                await bot.reply_text(
                    msg,
                    f"✅ 已切换到会话 {ctx.agent.session_id}" if ok
                    else f"❌ 找不到会话 {session_id}，请先发 /sessions 查看列表"
                )
            else:
                mgr = ctx.agent.session_manager
                if mgr and mgr.delete(session_id):
                    ctx.session_index = {k: v for k, v in ctx.session_index.items() if v != session_id}
                    await bot.reply_text(msg, f"🗑️ 已删除会话 {session_id}")
                else:
                    await bot.reply_text(msg, f"❌ 删除失败，会话不存在或 Session 功能未启用")
            return

        await bot.reply_text(msg, "用法：/session new | use <序号> | del <序号>")

    # ── 文件只读（直接调 Agent 内置工具，不走 HTTP）────────────────────────

    async def _call_tool(self, ctx: _UserCtx, tool_name: str, **kwargs) -> str:
        loop = asyncio.get_event_loop()

        def _run():
            td = ctx.agent.registry.get(tool_name)
            if td is None:
                return f"（工具 {tool_name!r} 不存在）"
            try:
                return str(td.fn(**kwargs))
            except Exception as exc:
                return f"（调用失败：{exc}）"

        return await loop.run_in_executor(self._executor, _run)

    async def _cmd_ls(
        self, bot: WeixinBot, msg: WeixinMessage, ctx: _UserCtx, path: str
    ) -> None:
        target = path.strip() or str(self._project_root)
        result = await self._call_tool(ctx, "list_dir", path=target)
        await bot.reply_text(msg, f"📁 {target}\n" + _truncate(result, MAX_REPLY_CHARS))

    async def _cmd_cat(
        self, bot: WeixinBot, msg: WeixinMessage, ctx: _UserCtx, path: str
    ) -> None:
        result = await self._call_tool(ctx, "read_file", path=path.strip())
        await bot.reply_text(msg, f"📄 {path}\n" + _truncate(result, MAX_FILE_CHARS))

    async def _cmd_find(
        self, bot: WeixinBot, msg: WeixinMessage, ctx: _UserCtx, query: str
    ) -> None:
        result = await self._call_tool(ctx, "glob", pattern=f"**/*{query.strip()}*")
        if not result or result.startswith("（"):
            await bot.reply_text(msg, f"🔍 未找到匹配 {query!r} 的文件")
        else:
            await bot.reply_text(msg, f"🔍 {query}\n" + _truncate(result, MAX_REPLY_CHARS))


# ── 入口 ─────────────────────────────────────────────────────────────────────

def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="微信端 mini_agent Bot（直接内嵌模式）")
    p.add_argument("--project", "-p", type=Path, default=None,
                   help="项目根目录（默认：脚本所在目录）")
    p.add_argument("--yes", "-y", action="store_true",
                   help="自动批准所有工具调用（危险）")
    p.add_argument("--verbose", "-v", action="store_true",
                   help="开启 debug 日志")
    return p.parse_args()


def main() -> None:
    args = _parse_args()

    log_level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(level=log_level, format="%(asctime)s %(levelname)s %(message)s")

    project_root = (args.project or _HERE).resolve()
    logger.info("项目根目录：%s", project_root)

    # ── 微信网关 ──────────────────────────────────────────────────────────────
    import os
    base_url = os.getenv("WEIXIN_BASE_URL", "")
    token    = os.getenv("WEIXIN_TOKEN", "")
    if base_url and token:
        bot = WeixinBot(base_url=base_url, token=token)
    else:
        # base_url, token = auto_token()
        
        account = load_or_login()  # 自动读取 ~/.weixin-bot/account.json 或引导扫码
        base_url = account.base_url
        token = account.token
        bot = WeixinBot(base_url=base_url, token=token)

    handler = WeixinHandler(project_root=project_root, auto_approve=args.yes)
    bot.add_handler(handler)

    logger.info("微信 mini_agent Bot 启动，网关=%s", base_url)
    asyncio.run(bot.run())


if __name__ == "__main__":
    main()
