"""
history/entry.py — history 条目类型系统

为每条 history 条目附加 _type 字段，消除字符串前缀猜测依赖。
每条追加到 raw history 的条目同时携带 _ts（ISO 8601 UTC 时间戳）。

类型枚举（HistoryEntryType）：
  user_input        — 真实用户输入（来自 REPL / API / CLI）
  user_correction    — 用户纠正（(e)dit 审批编辑产生，视为真实用户输入的子类，Stage 1.5）
  tool_result       — 工具执行结果回注（<tool_result> 格式）
  compressed        — 压缩占位符（[Previous conversation compressed]）
  compact_summary   — /compact 产生的 LLM 摘要占位符
  skill_context     — skill 上下文重附消息
  reminder          — 动态 reminder 注入
  role_agent        — role agent 反馈注入
  session_resume    — 跨 session 恢复标记（[Previous session summary]）
  hook_context      — hook 注入的额外上下文
  file_change       — 文件变化感知通知
  assistant_reply   — assistant 正常回复（role=assistant）

Raw history 专用类型（不出现在当前状态 history 中）：
  compact_event     — 记录发生了一次 compact，payload 包含压缩前后信息

时间戳：
  _ts 字段格式：ISO 8601 UTC，精确到毫秒，例如 "2024-01-15T08:30:00.123Z"
  active history 中的条目不含 _ts（避免污染 diff/快照比较）
  _ts 由 RawHistory.append() 自动注入（make_* 函数不注入）
  to_llm_messages() 同时剥离 _type 和 _ts

设计原则：
  - 写入存储时保留 _type 字段（磁盘格式含 _type）
  - raw history 在存储时保留 _ts 字段，active history 不含 _ts
  - 发给 LLM 前必须调用 to_llm_messages() 剥离 _type 和 _ts（保持 API 兼容）
  - _type / _ts 是内部字段，以下划线开头明确表示不对外暴露给模型
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional


class HType(str, Enum):
    """History 条目类型枚举。继承 str，可直接作为 JSON 字符串使用。"""

    def __str__(self) -> str:
        return self.value

    # ── user 侧消息 ──────────────────────────────────────────────────────────
    USER_INPUT       = "user_input"       # 真实用户输入
    USER_CORRECTION  = "user_correction"  # 用户纠正（(e)dit 审批编辑产生，Stage 1.5）
    TOOL_RESULT      = "tool_result"      # 工具结果回注
    SKILL_CONTEXT    = "skill_context"    # skill 上下文重附
    REMINDER         = "reminder"         # reminder 动态注入
    FORMAT_CORRECTION = "format_correction"  # 工具调用格式纠错提示（解析失败后自动注入）
    HOOK_CONTEXT     = "hook_context"     # hook 注入的额外上下文
    FILE_CHANGE      = "file_change"      # 文件变化通知（追加到用户消息）
    SESSION_RESUME   = "session_resume"   # 跨 session 恢复标记

    # ── assistant 侧消息 ─────────────────────────────────────────────────────
    ASSISTANT_REPLY  = "assistant_reply"  # 正常 assistant 回复

    # ── 压缩相关（当前状态 history）─────────────────────────────────────────
    COMPRESSED       = "compressed"       # auto-compress 产生的占位符（user 侧）
    COMPACT_SUMMARY  = "compact_summary"  # /compact 产生的 LLM 摘要（assistant 侧）
    # [compact_mechanism_improvement_plan P2-A] 压缩质量事后自检发现遗漏信息时，
    # 追加回历史的补充条目。role=user（系统对上一次压缩结果的补充说明，
    # 与 format_correction 一样借用 user 角色让模型能"看到"并据此调整）。
    COMPACT_SUPPLEMENT = "compact_supplement"

    # ── role agent ───────────────────────────────────────────────────────────
    ROLE_AGENT       = "role_agent"       # role agent 反馈注入

    # ── raw history 专用（不出现在当前状态 history 中）──────────────────────
    COMPACT_EVENT    = "compact_event"    # 记录发生了一次 compact 操作


# ── 判断辅助 ─────────────────────────────────────────────────────────────────

def is_real_user_input(msg: dict) -> bool:
    """判断一条 history 消息是否是真实的用户输入（而非 tool_result / 占位符等）。

    USER_CORRECTION（(e)dit 编辑产生）被视为真实用户输入的子类——它和
    USER_INPUT 一样代表用户主动表达的意图，只是来源渠道不同（编辑而非直接输入）。
    """
    t = msg.get("_type")
    if t is not None:
        return t in (HType.USER_INPUT, HType.USER_CORRECTION)
    # 向后兼容：无 _type 时用旧的字符串前缀判断
    content = msg.get("content", "")
    if not isinstance(content, str):
        return False
    return (
        msg.get("role") == "user"
        and not content.startswith("<tool_result")
        and not content.startswith("[Previous")
        and not content.startswith("[Compressed")
    )


def is_tool_result(msg: dict) -> bool:
    """判断一条消息是否是工具结果回注。"""
    t = msg.get("_type")
    if t is not None:
        return t == HType.TOOL_RESULT
    # 向后兼容
    content = msg.get("content", "")
    return (
        msg.get("role") == "user"
        and isinstance(content, str)
        and content.startswith("<tool_result")
    )


def is_compressed_placeholder(msg: dict) -> bool:
    """判断是否是压缩产生的占位符消息（compressed / compact_summary / session_resume）。"""
    t = msg.get("_type")
    if t is not None:
        return t in (HType.COMPRESSED, HType.COMPACT_SUMMARY, HType.SESSION_RESUME)
    # 向后兼容
    content = msg.get("content", "")
    if not isinstance(content, str):
        return False
    return content.startswith("[Previous") or content.startswith("[Compressed")


def is_turn_boundary(msg: dict) -> bool:
    """
    判断一条消息是否构成 turn 边界（可作为保留段起点的 user 消息）。

    turn 边界 = 真实用户输入，不包含 tool_result / 占位符 / reminder / skill_context 等。
    """
    if msg.get("role") != "user":
        return False
    t = msg.get("_type")
    if t is not None:
        return t in (HType.USER_INPUT, HType.USER_CORRECTION)
    # 向后兼容
    return is_real_user_input(msg)


def history_contains_tool_call(history: list[dict], tool_name: str) -> bool:
    """判断 history 中是否存在对某个工具的调用（assistant 消息里的 tool_use block）。

    用于 SessionEnd 阶段的轻量启发式判断（如 work_index.json 主动提醒：
    "本次 session 是否已经主动调用过 update_work_thread"），避免对已经
    记录过的 session 重复提醒。只看 content 是 list 且包含
    {"type": "tool_use", "name": ...} 的 assistant 消息，兼容 content 是
    纯字符串的旧格式/其他角色消息（直接跳过，不算作调用）。
    """
    for msg in history:
        if msg.get("role") != "assistant":
            continue
        content = msg.get("content")
        if not isinstance(content, list):
            continue
        for block in content:
            if isinstance(block, dict) and block.get("type") == "tool_use" and block.get("name") == tool_name:
                return True
    return False


# ── 时间戳辅助 ───────────────────────────────────────────────────────────────

def _now_ts() -> str:
    """返回当前本地时间的 ISO 8601 字符串，精确到毫秒，含时区偏移。

    例如：'2026-06-18T16:30:00.123+08:00'
    - 使用本地时间（对人类更直观，日志可读性好）
    - 含时区偏移（跨时区场景仍可排序比较，不丢失绝对时间信息）
    """
    now = datetime.now().astimezone()   # 本地时区
    ms = now.microsecond // 1000
    tz = now.strftime("%z")             # +0800
    tz_fmt = f"{tz[:3]}:{tz[3:]}" if len(tz) == 5 else "Z"  # +08:00
    return now.strftime("%Y-%m-%dT%H:%M:%S.") + f"{ms:03d}" + tz_fmt


# ── 构造辅助 ─────────────────────────────────────────────────────────────────
# make_* 函数只设置 role / content / _type，不注入 _ts。
# _ts 由 RawHistory.append() 统一注入，确保时间戳反映"写入 raw"的真实时刻。
# active history 条目不含 _ts，避免干扰快照比较和压缩策略。

def make_user_input(content: str) -> dict:
    return {"role": "user", "content": content, "_type": HType.USER_INPUT}


def make_user_correction(content: str) -> dict:
    """构造一条用户纠正消息（(e)dit 审批编辑产生，Stage 1.5）。

    对应设计文档 16.1 节：把编辑后的内容追加为一条 user 消息（_type="user_correction"），
    这条消息同时是真实用户输入（计入 history）和高质量的人类反馈信号（Stage 1.4 纠正检测会处理它）。
    """
    return {"role": "user", "content": content, "_type": HType.USER_CORRECTION}


def make_tool_result(content: str) -> dict:
    return {"role": "user", "content": content, "_type": HType.TOOL_RESULT}


def make_assistant_reply(content) -> dict:
    return {"role": "assistant", "content": content, "_type": HType.ASSISTANT_REPLY}


def make_compressed(content: str = "[Previous conversation compressed]") -> dict:
    return {"role": "user", "content": content, "_type": HType.COMPRESSED}


def make_compact_summary(content: str) -> dict:
    return {"role": "assistant", "content": content, "_type": HType.COMPACT_SUMMARY}


def make_compact_supplement(content: str) -> dict:
    """[compact_mechanism_improvement_plan P2-A] 压缩质量事后自检发现摘要遗漏
    决定性信息（约束条件/失败原因/用户明确要求等）时，把遗漏信息追加回历史。"""
    return {"role": "user", "content": content, "_type": HType.COMPACT_SUPPLEMENT}


def make_session_resume(content: str = "[Previous session summary]") -> dict:
    return {"role": "user", "content": content, "_type": HType.SESSION_RESUME}


def make_skill_context(content: str) -> dict:
    return {"role": "user", "content": content, "_type": HType.SKILL_CONTEXT}


def make_reminder(role: str, content: str) -> dict:
    return {"role": role, "content": content, "_type": HType.REMINDER}


def make_format_correction(content: str) -> dict:
    """构造一条工具调用格式纠错提示消息（_type=format_correction）。

    始终以 user 角色注入——这是系统对模型上一条输出的反馈，而非真实用户输入，
    但从 LLM 的对话轮次结构上看必须扮演 user 角色才能让模型"回应"它。
    """
    return {"role": "user", "content": content, "_type": HType.FORMAT_CORRECTION}


def make_role_agent(role: str, content: str) -> dict:
    return {"role": role, "content": content, "_type": HType.ROLE_AGENT}


def make_compact_event(
    before_count: int,
    after_count: int,
    strategy: str,
    trigger_reason: str = None,
) -> dict:
    """Raw history 专用：记录发生了一次 compact 操作。

    trigger_reason: 触发本次 compact 的原因标识，例如
        "token_threshold" / "turn_count" / "tool_call_count" /
        "topic_shift_heuristic" / "topic_shift_llm" / "redundancy" /
        "manual" / None（未知/旧调用点未传入）。
        用于事后统计各触发器的实际命中效果。
    """
    import json as _json
    payload = {
        "event": "compact",
        "before_count": before_count,
        "after_count": after_count,
        "strategy": strategy,
    }
    if trigger_reason:
        payload["trigger_reason"] = trigger_reason
    return {
        "role": "user",
        "content": _json.dumps(payload, ensure_ascii=False),
        "_type": HType.COMPACT_EVENT,
    }


# ── LLM 发送格式转换 ─────────────────────────────────────────────────────────

def to_llm_messages(history: list[dict]) -> list[dict]:
    """
    将带 _type / _ts 的 history 转换为发给 LLM 的格式（剥离内部元数据字段）。

    剥离字段：_type, _ts
    规则：
    - COMPACT_EVENT 条目（raw history 专用）不应出现在当前状态 history 中，
      但作为防御性处理，遇到时跳过

    此函数是 history → LLM API 的唯一出口，调用方无需手动过滤。
    """
    _STRIP = frozenset(["_type", "_ts"])
    result = []
    for msg in history:
        t = msg.get("_type")
        # COMPACT_EVENT 是 raw history 专用，不应出现在当前状态中，防御性跳过
        if t == HType.COMPACT_EVENT:
            continue
        # 剥离内部字段
        if any(k in msg for k in _STRIP):
            msg = {k: v for k, v in msg.items() if k not in _STRIP}
        result.append(msg)
    return result
