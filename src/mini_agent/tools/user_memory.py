"""
tools/user_memory.py — daemon 多用户架构 Phase 2：remember_about_user 工具

让 agent 在对话中主动记录"关于当前对话用户"的观察/备注，写入
RoleProfileManager 管理的 .agent/users/<user_id>/profile.json（agent_notes 字段）。

与 tools/workdir_knowledge.py / tools/evolution.py 的 thread-local provider
模式表面相似，但有一个关键区别，必须在这里说清楚：

  workdir_knowledge.py 的 set_project_root_provider/set_session_id_provider
  是在 Agent.__init__() 里注册的——project_root 和 session_id 在一个 Agent
  实例的生命周期内基本不变（session_id 只在 load_session()/new_session()
  时才变化，且变化后会重新读，不依赖"构造时刻"的快照）。

  但"当前是哪个用户在跟我说话"（user_id/role）是**逐条消息变化**的——
  在 Phase 1/2 的共享 Agent 模型下，同一个 Agent 实例在 t1 时刻服务 owner，
  t2 时刻可能服务一个 family 用户。如果照搬"在 Agent.__init__ 里注册一次"
  的写法，注册时根本不知道"下一条消息是谁发的"，这个 provider 永远是错的。

  所以这里改成：AgentRunner.run()（运行在它自己专属的后台线程上，
  不会跨线程）在每次调用 bridge.agent.run_turn() 之前，直接调用
  set_current_user(user_id, role) 把这一轮的身份写进 thread-local；
  run_turn() 同步执行完才会处理下一条消息，工具调用过程中读到的
  永远是"当前这一轮"的身份，不会读到上一轮或下一轮的。

  单用户模式下（role_profile_mgr 为 None）AgentRunner 根本不会调用
  set_current_user，thread-local 保持空，is_available() 返回 False，
  工具直接报错提示"当前不是多用户模式"，不会误写入任何文件。
"""

from __future__ import annotations

import threading as _threading
from typing import Optional

from . import tool

_user_ctx_local = _threading.local()

# 由 HttpServer.__init__ 在 multi_user_enabled 时注入；单用户模式下保持 None。
_role_profile_mgr = None


def set_role_profile_manager(mgr) -> None:
    """由 HttpServer.__init__ 调用一次，注入 RoleProfileManager 实例（或 None）。"""
    global _role_profile_mgr
    _role_profile_mgr = mgr


def set_current_user(user_id: str, role: str) -> None:
    """
    由 AgentRunner.run() 在每次调用 run_turn() 之前调用，写入"这一轮是谁发的"。
    必须在 AgentRunner 自己的线程上调用——同一个 run_turn() 调用期间，
    工具函数（包括下面的 remember_about_user）运行在同一线程上，
    读到的就是这次 set_current_user() 写入的值。
    """
    _user_ctx_local.user_id = user_id
    _user_ctx_local.role = role


def clear_current_user() -> None:
    """run_turn() 结束后调用，避免 thread-local 残留到下一次非用户触发的工具调用
    （例如 AutonomousLoop.tick() 也跑在同一条 AgentRunner 线程上）。"""
    _user_ctx_local.user_id = ""
    _user_ctx_local.role = ""


def _get_current_user() -> tuple[str, str]:
    return (
        getattr(_user_ctx_local, "user_id", "") or "",
        getattr(_user_ctx_local, "role", "") or "",
    )


def is_available() -> bool:
    """多用户模式已开启，且当前确实处于某个用户发起的 turn 内。"""
    user_id, _ = _get_current_user()
    return _role_profile_mgr is not None and bool(user_id)


@tool(
    name="remember_about_user",
    description=(
        "Record a short observation or note about the person you are currently "
        "talking to — a preference they mentioned, an important event, or a "
        "relationship detail worth remembering long-term. Only usable in "
        "multi-user mode, and only while talking to a non-owner user — the "
        "owner already has a separate, automatic personalization profile, so "
        "this tool should never be used to record notes about the owner. "
        "The note is saved to that person's own profile and will be surfaced "
        "again in future conversations with them, helping you keep context "
        "and continuity in the relationship."
    ),
    schema={
        "type": "object",
        "properties": {
            "note": {
                "type": "string",
                "description": (
                    "A short, specific note. Capture the gist, not a verbatim "
                    "transcript of anything sensitive or private."
                ),
            },
        },
        "required": ["note"],
    },
    requires_approval=False,  # 纯增量写入自己的画像文件，风险面与 add_open_thread 同级
)
def remember_about_user(note: str) -> str:
    user_id, role = _get_current_user()

    if _role_profile_mgr is None:
        return "Multi-user mode is not enabled; cannot record a user note."
    if not user_id:
        return "Not currently inside a turn initiated by a specific user; cannot tell who this note is about."
    if role == "owner":
        return (
            "The current conversation partner is the owner. The owner already has "
            "a separate, automatic personalization profile — this tool should not "
            "be used to record notes about them."
        )

    note = (note or "").strip()
    if not note:
        return "Note was empty; nothing recorded."

    try:
        _role_profile_mgr.add_agent_note(user_id, note)
    except Exception as e:
        from mini_agent.errors import log_exception
        log_exception(e, where='mini_agent.tools.user_memory.remember_about_user')
        return f"Failed to record note: {type(e).__name__}: {e}"

    return f"Recorded a note about user {user_id}."
