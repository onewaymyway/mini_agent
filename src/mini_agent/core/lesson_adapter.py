"""core/lesson_adapter.py — LessonAdapter：`MemoryEntry(entry_type="lesson")` -> `Experience`。

见 `next_doc/refactor_plan/04-phase3-experience-layer-sprint-plan.md`
“完成标志”第 2 条，以及 `docs/architecture_v2/phase3-experience-inventory.md`
第 2 节：代码库里没有独立的 `Lesson` 类，“lesson”是 `MemoryEntry` 的一个
`entry_type`，因此本 Adapter 的输入是 `MemoryEntry`，调用方负责按
`entry_type == "lesson"` 过滤（见 `core/lesson_import.py`），本 Adapter 对非
lesson 条目直接抛 `ValueError`，避免静默转换出语义错误的 Experience。

字段映射（旧 `MemoryEntry` → 新 `Experience`，对照原方案 §7）：

| Experience 字段 | 来源 | 说明 |
|---|---|---|
| id | `"lesson:" + entry_id` | 确定性 id，重复导入 upsert 为同一行（真正幂等） |
| source | 固定 `"memory_lesson"` | 与 Goal 链路产生的记录区分 |
| goal_text | `trigger`，为空时退回 `summary` | 检索按它做关键词重叠，trigger 是“触发场景” |
| status | 固定 `"lesson"` | **有意不用 failed/stuck**：Analyzer 的失败聚合只认 Goal 的失败状态，lesson 不应被重复计为一次 Goal 失败 |
| rounds_used | 0 | lesson 不是一次多轮执行 |
| final_report | `outcome`，为空时退回 `summary` | “实际发生了什么” |
| created_at | `created_at` | 沿用原时间戳 |
| lesson | `suggested_action`，为空时退回 `summary` | “下次该怎么做” |
| causal_hypothesis | `root_cause` | 旧结构只有根因，没有假设/验证之分，原样搬运 |
| confidence | `confidence` | 同名 |
| context | session_id / scope / tags / entry_id | 只放来源追溯信息 |
| evidence | memory_source / occurrence_count | 旧 `source` 字段（self_reflection/human_feedback/…）与重复次数 |

`action` / `reason` / `prediction` / `state_before` / `state_after` 保持默认值：
旧结构里没有对应信息，不臆造。

`to_old` 未实现：当前没有“从 Experience 反向写回 MemoryEntry”的调用方，
与 `SelfAdapter`/`HistoryAdapter`/`MemoryAdapter` 的处理方式一致，显式抛
`NotImplementedError` 并注明原因，不留无说明的空实现。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .adapter import Adapter
from .experience import Experience

if TYPE_CHECKING:
    from mini_agent.perception.memory_store import MemoryEntry

LESSON_ID_PREFIX = "lesson:"
LESSON_STATUS = "lesson"


class LessonAdapter(Adapter["MemoryEntry", Experience]):
    """`MemoryEntry`（lesson 型）-> `Experience` 的转换（当前只支持这一个方向）。"""

    @staticmethod
    def to_new(old: "MemoryEntry") -> Experience:
        if getattr(old, "entry_type", "") != "lesson":
            raise ValueError(
                f"LessonAdapter 只转换 entry_type='lesson' 的条目，"
                f"收到 entry_type={getattr(old, 'entry_type', None)!r}"
            )
        summary = old.summary or ""
        return Experience(
            source="memory_lesson",
            goal_text=old.trigger or summary,
            status=LESSON_STATUS,
            rounds_used=0,
            final_report=old.outcome or summary,
            created_at=float(old.created_at),
            id=f"{LESSON_ID_PREFIX}{old.entry_id}",
            context={
                "session_id": old.session_id,
                "scope": old.scope,
                "tags": list(old.tags),
                "entry_id": old.entry_id,
            },
            lesson=old.suggested_action or summary,
            causal_hypothesis=old.root_cause or "",
            confidence=float(old.confidence),
            evidence={
                "memory_source": old.source,
                "occurrence_count": int(old.occurrence_count),
            },
        )

    @staticmethod
    def to_old(new: Experience) -> "MemoryEntry":
        # 没有调用方需要“Experience -> MemoryEntry”：lesson 的写入点仍在旧模块
        # （reflection / reminders_correction / outcome_tracker / failure_pattern_store），
        # 新链路只做单向导入。等出现真实调用方再实现，避免为不存在的需求设计。
        raise NotImplementedError(
            "LessonAdapter.to_old 尚未实现：当前没有从 Experience 反向构造 "
            "MemoryEntry 的调用方（见 core/lesson_adapter.py 顶部说明）。"
        )
