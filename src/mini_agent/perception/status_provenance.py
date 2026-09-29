"""
perception/status_provenance.py — 状态变更来源（provenance）记录

背景（详见 next_doc/goal_cron_paused_semantics_and_status_provenance_plan.md）：
`GoalNode.status_history` 此前只记录 `{"status", "at"}`，能查到"什么时候变成了
paused"，查不到"是谁改的"。周期性 Goal 被静默停摆 8 天后，无法判断是用户在看板上
点的、CLI 命令、cron 自愈、还是 SoftGoalDeriver 的自动复核。

本模块提供一个很小的共享词表和几个纯函数，供 GoalBacklog（Goal 状态）与
CronScheduler（job 启停）共用同一套"来源"表达：

  actor 字符串格式：`<类别>:<具体入口>`，类别只有五种——
    user     用户主动操作（CLI 命令、REST/看板）
    cron     cron 调度器/goal_cycle 触发逻辑自动改的
    system   其它内部自动机制（执行器回写、失败重试、外部信号拉起、自动复核）
    agent    agent 自己在对话里调用工具改的（预留，目前没有专用入口）
    unknown  调用方没有声明来源——此时会自动附带 caller（调用点），用于追查

设计取舍：
- 只追加、不影响主流程：本模块任何函数失败都退化为 "unknown"，不抛异常。
- 调用方不声明 actor 时不报错（保持所有既有调用点行为不变），而是自动抓取调用点
  `module.function:line`，这样即使有我们没预料到的入口在改状态，事后也能追到。
"""

from __future__ import annotations

import sys
import time
from typing import Any, Optional

# ── 词表 ────────────────────────────────────────────────────────────────────

ACTOR_USER_CLI = "user:cli"
ACTOR_USER_API = "user:api"
ACTOR_CRON_GOAL_CYCLE = "cron:goal_cycle"
ACTOR_CRON_SCHEDULER = "cron:scheduler"
ACTOR_SYSTEM_OBJECTIVE_EXECUTOR = "system:objective_executor"
ACTOR_SYSTEM_GOAL_NODE_RETRY = "system:goal_node_retry"
ACTOR_SYSTEM_GOAL_RELEVANCE = "system:goal_relevance"
ACTOR_SYSTEM_SOFT_GOAL_DERIVER = "system:soft_goal_deriver"
ACTOR_SYSTEM_GOAL_RECURRENCE = "system:goal_recurrence"
ACTOR_UNKNOWN = "unknown"

ACTOR_KINDS = ("user", "cron", "system", "agent", "unknown")

_ACTOR_LABELS = {
    "user": "用户操作",
    "cron": "cron 自动",
    "system": "系统自动",
    "agent": "agent 调用",
    "unknown": "未声明来源",
}

# status_history / state_history 单个节点最多保留的条数。只追加不删改是
# 既有约定，这里仅在超长时丢弃最旧的，避免 goals.json 无限膨胀。
HISTORY_MAX_ENTRIES = 200


def actor_kind(actor: Optional[str]) -> str:
    """`user:cli` → `user`；空值/未知类别 → `unknown`。"""
    if not actor:
        return "unknown"
    kind = str(actor).split(":", 1)[0].strip().lower()
    return kind if kind in ACTOR_KINDS else "unknown"


def describe_actor(actor: Optional[str]) -> str:
    """人类可读的来源说明，如 `用户操作（user:cli）`。"""
    if not actor:
        return f"{_ACTOR_LABELS['unknown']}（unknown）"
    return f"{_ACTOR_LABELS[actor_kind(actor)]}（{actor}）"


def capture_caller(skip_prefixes: tuple[str, ...] = ("mini_agent.perception.goal_backlog",
                                                      "mini_agent.perception.status_provenance",
                                                      "mini_agent.evolution.cron_scheduler")) -> str:
    """抓取最近一个"不在写入原语内部"的调用点，格式 `module.function:line`。

    仅在调用方没有声明 actor 时使用。沿调用栈向上跳过 skip_prefixes 里的模块
    （GoalBacklog/CronScheduler 自身的内部帧），取第一个外部调用帧。任何异常
    都返回空字符串——追溯信息缺失不能影响状态写入本身。
    """
    try:
        frame = sys._getframe(1)
        while frame is not None:
            module = frame.f_globals.get("__name__", "")
            if not any(module.startswith(p) for p in skip_prefixes):
                return f"{module}.{frame.f_code.co_name}:{frame.f_lineno}"
            frame = frame.f_back
    except Exception:
        pass
    return ""


def make_history_entry(
    *,
    status: Any,
    previous: Any = None,
    actor: Optional[str] = None,
    reason: str = "",
    status_key: str = "status",
    previous_key: str = "from",
) -> dict:
    """构造一条带来源的历史记录。

    - `status_key`：Goal 用 "status"（字符串），CronJob 用 "enabled"（布尔）。
    - 兼容旧格式：旧记录只有 `{"status", "at"}`，新记录在此基础上追加
      `from`/`by`/`reason`/`caller`，读取方按 `.get()` 处理缺失字段即可。
    - actor 缺失时记为 "unknown" 并附带 caller（调用点），便于追查。
    """
    entry: dict[str, Any] = {status_key: status, "at": time.time()}
    if previous is not None:
        entry[previous_key] = previous
    if actor:
        entry["by"] = actor
    else:
        entry["by"] = ACTOR_UNKNOWN
        caller = capture_caller()
        if caller:
            entry["caller"] = caller
    if reason:
        entry["reason"] = str(reason)[:200]
    return entry


def append_history(history: Any, entry: dict) -> list:
    """返回追加了 entry 的新列表（不原地修改），并按 HISTORY_MAX_ENTRIES 截断。"""
    items = list(history or []) + [entry]
    if len(items) > HISTORY_MAX_ENTRIES:
        items = items[-HISTORY_MAX_ENTRIES:]
    return items


def last_entry_with(history: Any, **match: Any) -> Optional[dict]:
    """倒序找第一条满足全部键值的记录（如 `status="paused"`），找不到返回 None。"""
    for item in reversed(list(history or [])):
        if isinstance(item, dict) and all(item.get(k) == v for k, v in match.items()):
            return item
    return None


def format_history_entry(entry: dict, *, status_key: str = "status") -> str:
    """单条记录渲染成一行文本，供 CLI/通知使用。旧格式记录（无 by）显示"未记录来源"。"""
    at = entry.get("at")
    stamp = time.strftime("%m-%d %H:%M:%S", time.localtime(at)) if at else "?"
    prev = entry.get("from")
    cur = entry.get(status_key)
    arrow = f"{prev} → {cur}" if prev is not None else f"→ {cur}"
    by = entry.get("by")
    who = describe_actor(by) if by else "未记录来源（旧数据）"
    line = f"[{stamp}] {arrow}　by {who}"
    if entry.get("caller"):
        line += f"　caller={entry['caller']}"
    if entry.get("reason"):
        line += f"　原因：{entry['reason']}"
    return line
