"""perception/turn_context.py — 当前轮次发起者（initiator）的 thread-local 透传

背景：Goal 溯源改进（见 docs/goal-provenance-guide.md）。

`GoalBacklog.add_goal()` 一直有 `source` 字段区分"谁负责决定要建这个
Goal"（user 手动 / agent_derived 自动派生 / novelty_candidate 新颖信号
确认），但没有任何字段记录"这个 Goal 是在哪一轮对话里、由谁触发的这轮
对话中被创建的"。

这是一个真实的空白：`external_input/goal_relevance.py::run_goal_relevance_judge_once()`
在判定某个已有 Goal "advance_worthy" 时，会调用 `enqueue_fn` 把一条
"请判断目标是否需要推进、下一步该做什么"的消息，以
`initiator="cron"` 的身份提交进 `InputQueue`。这轮对话对 Agent 来说
和用户手动发消息没有任何区别——Agent 在这轮对话里如果通过任意工具/
命令（比如运行 shell 里的 `mini_agent goals add ...`）创建了一个新
Goal，那次 `add_goal()` 调用完全不知道"这轮对话其实是 cron 触发的，
不是用户在打字"，于是 `source` 只会退回默认值 `"user"`，看起来跟真正
用户手动建的 Goal 没有区别。

本模块用跟 `tools/user_memory.py::set_current_user()` 完全相同的
thread-local 模式解决这个问题：`AgentRunner._main_loop()` 在把
`cmd.message` 交给 Agent 处理之前，调用 `set_current_turn_initiator()`
把这一轮 `InputQueue` 的 `initiator` 写进 thread-local；`GoalBacklog.
add_goal()` 在调用方没有显式传 `source_initiator` 时，会读取这个
thread-local 作为兜底值。轮次处理结束后清空，避免残留到下一次不是
由任何轮次触发的调用（比如 AutonomousLoop.tick() 内部直接调用
add_goal()，那种情况调用方本来就会显式传 source_initiator，不依赖
这里的兜底）。

单用户模式 / CLI 场景下没有 AgentRunner 主循环、也不会调用
`set_current_turn_initiator()`，thread-local 保持空，
`get_current_turn_initiator()` 返回默认值 `"user"`——这正好符合
"CLI 命令行手动敲的 `/goal add` 就是用户本人操作"的语义，不需要
额外处理。
"""

from __future__ import annotations

import threading as _threading

_turn_ctx_local = _threading.local()

DEFAULT_INITIATOR = "user"


def set_current_turn_initiator(initiator: str, turn_id: str = "") -> None:
    """由 AgentRunner._main_loop() 在把一轮消息交给 Agent 处理之前调用。
    必须在处理这一轮消息的同一条线程上调用——工具函数/命令处理过程中
    运行在同一线程，读到的就是这里写入的值。"""
    _turn_ctx_local.initiator = initiator or DEFAULT_INITIATOR
    _turn_ctx_local.turn_id = turn_id


def clear_current_turn_initiator() -> None:
    """一轮处理结束后调用，避免 thread-local 残留到下一次非轮次触发的调用
    （例如 AutonomousLoop.tick() 也跑在同一条 AgentRunner 线程上）。"""
    _turn_ctx_local.initiator = DEFAULT_INITIATOR
    _turn_ctx_local.turn_id = ""


def get_current_turn_initiator() -> str:
    """返回当前线程正在处理的轮次的 initiator；未设置（CLI/测试/无主循环
    场景）时返回 DEFAULT_INITIATOR（"user"）。"""
    return getattr(_turn_ctx_local, "initiator", DEFAULT_INITIATOR) or DEFAULT_INITIATOR


def get_current_turn_id() -> str:
    return getattr(_turn_ctx_local, "turn_id", "") or ""


__all__ = [
    "set_current_turn_initiator",
    "clear_current_turn_initiator",
    "get_current_turn_initiator",
    "get_current_turn_id",
    "DEFAULT_INITIATOR",
]
