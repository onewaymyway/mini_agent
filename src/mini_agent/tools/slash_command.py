"""
tools/slash_command.py — run_slash_command 工具

背景（根因）：
    cron/自主任务提交给 agent 的消息经常是"自然语言 + 内嵌 slash 命令"的形式，
    例如 "[推荐] 执行一次 /next refresh，重新计算候选并排序，如果没有候选则跳过"。
    这类消息不是以 "/" 开头，api/server.py::_main_loop() 的快速路径识别不到，
    会被当成普通聊天内容整段丢给 agent.run_turn()。

    agent 拿到这种指令后，此前完全没有"直接执行 REPL 内建 slash 命令"的手段，
    只能自己"猜"：翻代码、发现 /next 是 REPL 命令后，试图用
    `echo "/next refresh" | python main.py` 这种方式另起一个全新进程/session
    去跑——新进程和当前正在运行的 agent 状态完全无关，日志目录里也不会有真实的
    执行记录，本质上是"假装执行成功"，而且额外的子进程还可能拖慢/卡住主循环。

    真正的根因不是 cron 的 task_template 写法（那只是触发方式之一），而是
    agent 本身缺一个"我知道 /next /debug /digest 这些是什么、我可以直接调用"
    的能力。本模块补上这个能力：注册一个 run_slash_command 工具，内部直接复用
    cli/repl.py::_handle_slash()（与用户在 REPL 里手敲命令走的是同一套分发
    逻辑），配合 white/deny 列表限制可执行范围，避免自主任务通过这个工具做
    危险操作（切换 session、清空历史、退出进程等）。

用法（工具 schema 会告诉模型）：
    run_slash_command(command="/next refresh")
    run_slash_command(command="/digest daily")
    run_slash_command(command="/debug history 20")

设计要点：
  - requires_approval=False：这个工具本质上只是"帮你在当前会话里直接执行一条
    你已经决定要跑的命令"，不引入新的副作用面（副作用面等同于对应的 slash
    命令本身）；如果每次都要人工审批，cron/自主任务场景就没法用了，等于白修。
  - 但不是"什么命令都能跑"：DENY_PREFIXES 里的命令会被拒绝，避免 agent
    借这个工具切 session、清空/回滚历史、改模型、退出进程等——这些操作要么
    需要人工决定，要么本身就有专门的、更受控的工具/审批路径。
  - 输出通过 Terminal.run_captured() 捕获，与 daemon 侧 HTTP slash 命令走的
    捕获机制完全一致（见 api/server.py::AgentRunner._main_loop 里同样的用法），
    保证行为一致、不会漏挂载 SSE 实时中继。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from mini_agent.tools import ToolRegistry
    from mini_agent.agent.core import Agent


# 明确拒绝：会改变会话身份/生命周期，或需要人工交互式确认的命令。
# 前缀匹配（如 "session" 会拦掉 "/session new"、"/session list" 等所有子命令）。
_DENY_PREFIXES = (
    "exit", "quit",
    "session",      # 切换/新建 session 应由人决定，不该被自主任务悄悄切走
    "clear",        # 清空历史，破坏性且不可逆
    "rollback",     # 回退整轮，可能撤掉正在依赖的上下文
    "model",        # 切换模型这种全局配置改动应保持人工可控
    "role",         # persona 切换同理
    "platform",     # 平台/tag 过滤策略这种全局开关同理
)


def _is_denied(name: str) -> bool:
    name = name.lower()
    return any(name == p or name.startswith(p + " ") or name.startswith(p) for p in _DENY_PREFIXES)


def register_slash_command_tool(registry: "ToolRegistry", agent: "Agent") -> None:
    """在 Agent.__init__ 尾部调用（需要 agent 实例 + skill_loader）。"""

    def run_slash_command(command: str) -> str:
        """
        Execute a built-in REPL slash command (e.g. "/next refresh", "/digest daily",
        "/debug history 20") directly in the current session, and return its output.

        Use this whenever your task (including autonomous/cron-triggered tasks)
        describes running a specific "/xxx" command — call this tool with that
        exact command instead of trying to reverse-engineer or spawn a subprocess
        to run it; spawning a new process starts a brand-new, unrelated session
        and will NOT actually perform the action.

        Not all commands are available here: session switching, history clearing/
        rollback, model/role/platform changes, and exit/quit are rejected — those
        need a human decision or a dedicated tool.
        """
        cmd = (command or "").strip()
        if not cmd.startswith("/"):
            cmd = "/" + cmd
        parts = cmd.lstrip("/").split()
        name = parts[0].lower() if parts else ""

        if not name:
            return json.dumps({"status": "error", "message": "empty command"}, ensure_ascii=False)

        if _is_denied(name):
            return json.dumps({
                "status": "denied",
                "message": (
                    f"'/{name}' 不允许通过 run_slash_command 执行（会改变 session/"
                    f"历史/模型等全局状态，需要人工在 REPL 里手动操作）。"
                ),
            }, ensure_ascii=False)

        try:
            from mini_agent.cli.repl import _handle_slash
            from mini_agent.ui.terminal import term as _term_singleton

            def _run() -> None:
                _handle_slash(cmd, agent, getattr(agent, "skill_loader", None))

            output = _term_singleton.run_captured(_run).strip()
            return json.dumps({
                "status": "ok",
                "command": cmd,
                "output": output or "(no output)",
            }, ensure_ascii=False)
        except Exception as e:
            from mini_agent.errors import log_exception
            log_exception(e, where="mini_agent.tools.slash_command.run_slash_command")
            return json.dumps({"status": "error", "message": str(e)}, ensure_ascii=False)

    registry.register_fn(
        fn=run_slash_command,
        name="run_slash_command",
        description=(
            "Directly execute a built-in REPL slash command (e.g. '/next refresh', "
            "'/digest daily', '/debug history 20') in the current session and return "
            "its output. Prefer this over guessing / spawning a subprocess whenever "
            "a task description references a specific '/xxx' command."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "command": {
                    "type": "string",
                    "description": "The slash command to run, e.g. '/next refresh'.",
                }
            },
            "required": ["command"],
        },
        requires_approval=False,
        group="builtin",
        tags=["autonomous_safe"],
    )
