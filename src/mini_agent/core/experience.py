"""core/experience.py — Experience 定义。

Sprint 1/2（见 `02-executable-sprint-plan.md`）落地的 `Experience` 只有
`source/goal_text/status/rounds_used/final_report/created_at` 六个字段，
够用来演示 `Goal → Action → Outcome → Experience` 最小闭环，但没有覆盖
原方案 §7（`next_doc/refactor_plan/00-original-architecture-proposal.md`）
定义的完整 yaml 结构：

    experience:
      id / timestamp / context / state_before / goal / action / reason /
      prediction / outcome / state_after / evidence / lesson /
      causal_hypothesis / confidence

Phase 3 Sprint 3-1（见
`next_doc/refactor_plan/04-phase3-experience-layer-sprint-plan.md`）在此
基础上**扩展**（不是替换）——沿用 `core/events.py` 扩展 `Event` 字段集时
定下的先例：保留旧字段名不变（`goal_text`/`status`/`final_report`/
`created_at`），新增字段全部给默认值，保证旧调用点（`goal_adapter.py`
Sprint 1 已有的赋值、`experience_store.py`/`experience_cmd.py` 已有的
读取）不改一行也能继续工作。新旧字段的对应关系（供阅读时对照 §7）：

| §7 字段 | 本文件对应字段 | 说明 |
|---|---|---|
| id | id（新增） | UUID，默认自动生成 |
| timestamp | created_at（Sprint 1 已有） | 沿用旧字段名，不新增同义字段 |
| goal | goal_text（Sprint 1 已有） | 同上 |
| outcome | status + final_report（Sprint 1 已有） | §7 的 outcome 拆成了这两个更具体的旧字段，不合并 |
| context / state_before / action / reason / prediction /
  state_after / evidence / lesson / causal_hypothesis / confidence | 同名新增字段 | Sprint 1/2 没有覆盖到的部分，本次补齐 |
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .types import ExperienceSource


@dataclass
class Experience:
    """一次重要行为（目前只有 Goal 执行）沉淀下来的经验记录。"""

    # ── Sprint 1/2 已有字段（字段名不变，见本文件顶部说明）──────────────
    source: ExperienceSource
    goal_text: str
    status: str
    rounds_used: int
    final_report: str
    created_at: float = field(default_factory=time.time)

    # ── Phase 3 Sprint 3-1 新增：补齐原方案 §7 yaml 结构剩余字段 ────────
    id: str = field(default_factory=lambda: uuid.uuid4().hex)
    context: dict = field(default_factory=dict)
    state_before: dict = field(default_factory=dict)
    action: str = ""
    reason: str = ""
    prediction: str = ""
    state_after: dict = field(default_factory=dict)
    evidence: dict = field(default_factory=dict)
    lesson: str = ""
    causal_hypothesis: str = ""
    confidence: Optional[float] = None

    def to_dict(self) -> dict:
        return {
            "source": self.source,
            "goal_text": self.goal_text,
            "status": self.status,
            "rounds_used": self.rounds_used,
            "final_report": self.final_report,
            "created_at": self.created_at,
            "id": self.id,
            "context": self.context,
            "state_before": self.state_before,
            "action": self.action,
            "reason": self.reason,
            "prediction": self.prediction,
            "state_after": self.state_after,
            "evidence": self.evidence,
            "lesson": self.lesson,
            "causal_hypothesis": self.causal_hypothesis,
            "confidence": self.confidence,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "Experience":
        """从 `to_dict()` 产出的 dict（或旧版本只有六个字段的 dict）还原。

        对旧数据（Sprint 1/2 阶段写入、没有新增字段的记录）宽容处理：
        缺失的新增字段全部回退到 dataclass 默认值，不因为“字段缺失”
        而抛异常——这是 `experience_store.py` 升级到 SQLite 后仍需要能
        读取迁移脚本导入的旧数据的前提。
        """
        kwargs: dict = {
            "source": d.get("source", "goal_mode"),
            "goal_text": d.get("goal_text", ""),
            "status": d.get("status", ""),
            "rounds_used": int(d.get("rounds_used", 0)),
            "final_report": d.get("final_report", ""),
            "created_at": float(d.get("created_at", 0.0)),
        }
        if d.get("id"):
            kwargs["id"] = d["id"]
        for k in (
            "context",
            "state_before",
            "action",
            "reason",
            "prediction",
            "state_after",
            "evidence",
            "lesson",
            "causal_hypothesis",
            "confidence",
        ):
            if k in d and d[k] is not None:
                kwargs[k] = d[k]
        return cls(**kwargs)
