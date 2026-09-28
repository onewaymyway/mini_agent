"""core/capability.py — CapabilityState（Phase 10 S-A A2：从占位填为真实字段）。

见 `next_doc/refactor_plan/14-phase10-sa-item-plan.md` 第二节。原占位文件（Phase 4
Sprint 4-2）要求“Phase 6 统一 Action 之后再回填真实字段”。Phase 6 之后，Agent 能
“做什么”在代码里实际来自三个注册表，本状态只是它们的**只读名字快照**（字段与来源均已
核对过真实 API，见 `core/capability_projector.py`）：

| 字段 | 来源 |
|---|---|
| tools | `ToolRegistry.names()` |
| skills_available / skills_active | `SkillLoader.available` / `SkillLoader.active` |
| workflows | `WorkflowStore.list_all()` 每项的 `name` |
| refreshed_at | 生成快照的时间戳 |

只存名字，不存工具 schema / skill 内容 / workflow 步骤——它们的详情仍在各自注册表里，
本状态不是第二份真相来源，只是给领域模型一个“当前有哪些能力”的统一读口。

原方案 §9 里的 SubAgent / Strategy / Knowledge / Environment Access / Learned
Procedure 等类别**目前没有对应的真实注册表**，不在此定义。
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Optional


@dataclass
class CapabilityState:
    """当前可用能力的只读名字快照。"""

    tools: list = field(default_factory=list)
    skills_available: list = field(default_factory=list)
    skills_active: list = field(default_factory=list)
    workflows: list = field(default_factory=list)
    refreshed_at: Optional[float] = None

    def to_dict(self) -> dict:
        return asdict(self)
