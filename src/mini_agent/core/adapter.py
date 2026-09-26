"""core/adapter.py — Adapter[Old, New] 最小协议。

见 `next_doc/refactor_plan/02-executable-sprint-plan.md` Sprint 1：
"定义 Adapter 契约"任务的产出。这是后续所有 Adapter（Goal / Memory /
Self，按迁移链启动顺序逐个补）的统一接口，Sprint 1 只落地 Goal 这一个
实现（`GoalAdapter`，见 `core/goal_adapter.py`）。

止损条件（见 `02-executable-sprint-plan.md` Sprint 1）：如果 Adapter
双向转换在 Goal 这一个子系统上就出现明显的"数据丢失"或"来回转换不
一致"问题，暂停向 Memory 推广，先在 Sprint 1 内部把协议改稳——本协议
本身应保持足够简单（只有两个方法），避免协议层本身成为新的耦合来源。
"""

from __future__ import annotations

from typing import Generic, Protocol, TypeVar

Old = TypeVar("Old")
New = TypeVar("New")


class Adapter(Protocol, Generic[Old, New]):
    """旧模块领域对象 <-> 新领域模型对象的双向转换协议。"""

    @staticmethod
    def to_new(old: Old) -> New:
        """把旧模块里的对象转换成新领域模型对象。"""
        ...

    @staticmethod
    def to_old(new: New) -> Old:
        """把新领域模型对象转换回旧模块能理解的对象。"""
        ...
