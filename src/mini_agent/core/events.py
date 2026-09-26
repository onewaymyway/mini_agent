"""core/events.py — 最小 Event 定义。

统一 Event Model 是 Phase 2（见
`next_doc/refactor_plan/03-phase2-event-model-sprint-plan.md`）的范围，
本文件不提前实现 Phase 2 的完整事件总线/订阅机制。

Sprint 1 只需要一个足够记录"Goal 链路每次接入 Adapter 时发生了什么"的
最小 Event dataclass，用于产出 Sprint 1 验收标准要求的
"trace 证据"（见 `02-executable-sprint-plan.md` Sprint 1 验收标准第 1 条：
"有日志/trace 能证明这条链真的被执行过"）。

TODO（Phase 2 填充时机）：Phase 2 启动时，把本文件替换/扩展为完整的
Event Model（事件总线、订阅、路由），届时需要评估是否需要对
`kind`/`payload` 字段做 schema 化改造；在此之前，`Event` 只作为
"trace 记录点"使用，不承担事件分发职责。
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class Event:
    """一条最小的 trace 事件：谁、在什么时候、发生了什么。"""

    kind: str
    payload: dict = field(default_factory=dict)
    at: float = field(default_factory=time.time)

    def to_dict(self) -> dict:
        return {"kind": self.kind, "payload": self.payload, "at": self.at}
