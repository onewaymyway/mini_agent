"""core/lesson_import.py — 把旧 lesson 型 MemoryEntry 导入 ExperienceStore。

**只在被显式调用时执行**（由 `scripts/import_lessons_to_experience.py` 触发），
不挂任何运行时钩子：没有人调用它，系统行为与本次改动前完全一致（保守 opt-in）。
本模块不 import `perception/` 任何具体实现，只依赖“有 `all_entries()` 的对象”，
所以不增加对 `memory_store.py` 的 inbound 耦合。

幂等：`LessonAdapter` 产出确定性 id（`lesson:<entry_id>`），`ExperienceStore.append()`
按 id upsert，重复导入不会产生重复行。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

from .experience_store import ExperienceStore
from .lesson_adapter import LessonAdapter


@dataclass
class LessonImportResult:
    scanned: int = 0    # 遍历到的条目总数
    imported: int = 0   # 写入（含覆盖）的 lesson 条目数
    skipped: int = 0    # 非 lesson 条目数
    failed: int = 0     # 转换或写入失败的 lesson 条目数


def import_lessons(entries: Iterable[Any], store: ExperienceStore) -> LessonImportResult:
    """把 `entries` 里 `entry_type == "lesson"` 的条目逐条转换并写入 `store`。

    单条失败不影响其余条目（计入 `failed`），因为这是批量历史数据导入。
    """
    result = LessonImportResult()
    for entry in entries:
        result.scanned += 1
        if getattr(entry, "entry_type", "") != "lesson":
            result.skipped += 1
            continue
        try:
            store.append(LessonAdapter.to_new(entry))
            result.imported += 1
        except Exception:
            result.failed += 1
    return result
