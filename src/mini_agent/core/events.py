"""core/events.py — Event 定义。

Sprint 1（见 `02-executable-sprint-plan.md`）只需要一个足够记录"Goal 链路
每次接入 Adapter 时发生了什么"的最小 Event dataclass，用于产出 Sprint 1
验收标准要求的"trace 证据"。当时的 `Event` 只有 `kind/payload/at` 三个
字段，且不承担事件分发职责（只被塞进日志 `_core_logger.debug(...)`）。

Phase 2 Sprint 2-1（见
`next_doc/refactor_plan/03-phase2-event-model-sprint-plan.md`）在此基础上
**扩展**（不是替换）出原文 §35 要求的完整字段集：`id / actor / context /
causation_id / correlation_id`，用于支撑 Sprint 2-2 的因果链路追踪
（`events trace <correlation_id>`）。

刻意保留 `kind`（而不是改名成原文用词 `type`）+ `at`（而不是改名成
`timestamp`）两个字段名不变：仓库里已有多处旧调用点（`goal_mode/runner.py`
Sprint 1 接入点、`agent/core.py`、`agent/lifecycle.py`、多个
`tests/test_core_*_adapter.py`）用位置/关键字参数构造 `Event(kind=...,
payload=...)`，改名会导致这些调用点全部需要跟着改，属于"侵入既有代码"，
与 Sprint 2-1 止损条件（"不应改变原有业务逻辑"）相悖。新增字段全部给了
默认值，保证旧调用点不改一行也能继续工作。

`EVENT_KINDS`：Sprint 2-1 范围内先只落地 Phase 1 已经用到的几种取值
（`GoalCreated / GoalUpdated / ActionStarted / ActionCompleted /
ActionFailed / ExperienceCreated`），其余（`ToolCalled` / `WorldChanged` /
`SelfChanged` / ...）留到对应 Phase 真正启动迁移链时再加，不一次性把
原文 §35 提到的 15 种全部定义成空壳常量。这是一组"建议取值"而非强校验的
枚举——`kind` 字段本身仍是自由字符串，本仓库里 Sprint 1 就已经在用
`"goal_mode.adapter.to_new"` 这类命名空间式的 `kind`，Sprint 2-1 不强制
迁移这些既有取值去对齐 `EVENT_KINDS`，只要求**新增**的"一次 Goal 闭环"
埋点使用 `EVENT_KINDS` 里的取值（见 `goal_mode/runner.py` 里
`# [Phase 2 Sprint 2-1]` 标注的位置）。
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Optional


# Sprint 2-1 落地的最小事件类型集合（原文 §35 结构里的一个子集，见本文件
# 顶部说明）。新增类型时先看是否已有等价含义的旧 kind 字符串，避免重复
# 定义"名字不同、含义相同"的两种 kind。
EVENT_KINDS: tuple[str, ...] = (
    "GoalCreated",
    "GoalUpdated",
    "ActionStarted",
    "ActionCompleted",
    "ActionFailed",
    "ExperienceCreated",
)


@dataclass
class Event:
    """一条事件：谁、在什么时候、因为什么、发生了什么。

    字段对齐原文 §35（`id / type / timestamp / actor / context / payload /
    causation_id / correlation_id`），但沿用 Sprint 1 已经在用的字段名
    `kind`（对应 `type`）与 `at`（对应 `timestamp`），理由见本文件顶部
    说明。
    """

    kind: str
    payload: dict = field(default_factory=dict)
    at: float = field(default_factory=time.time)
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    actor: Optional[str] = None
    context: dict = field(default_factory=dict)
    causation_id: Optional[str] = None
    correlation_id: Optional[str] = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "payload": self.payload,
            "at": self.at,
            "id": self.id,
            "actor": self.actor,
            "context": self.context,
            "causation_id": self.causation_id,
            "correlation_id": self.correlation_id,
        }
