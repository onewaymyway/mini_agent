"""evolution/memory_types.py — evolution/ 包内部的 MemoryEntry 访问门面。

见 `next_doc/refactor_plan/03-sprint1.5-memory-perception-coupling-assessment.md`
"三、迁移优先级建议"第 3 条 + "九、perception/memory_store.py 分层
facade · 第一层（evolution/）执行记录"：`perception/memory_store.py`
inbound 深度依赖清理死代码后仍有 24（超止损阈值 10+），需要按"分层加
facade"的方式逐层收敛，而不是像 `history_manager.py` 那样一次性直接
做 Adapter。本文件是第一层：`evolution/` 包内部 6 个文件
（`consolidation.py`/`failure_pattern_store.py`/`memory_aging.py`/
`memory_backfill.py`/`memory_consolidation.py`/`outcome_tracker.py`）
原先各自直接 `from mini_agent.perception.memory_store import
MemoryEntry`，且**只用到 `MemoryEntry` 这一个类型**（不涉及
`MemoryStore` 本身的读写方法），收敛为统一从本文件 import，减少
`perception/memory_store.py` 的 inbound 扇出。

本文件只做"重新导出"（re-export），不改变 `MemoryEntry` 的定义或
行为，也不是新的领域概念——`evolution/` 包内部其它文件应该从这里
import `MemoryEntry`，而不是各自直接指向
`mini_agent.perception.memory_store`。
"""

from __future__ import annotations

from mini_agent.perception.memory_store import MemoryEntry

__all__ = ["MemoryEntry"]
