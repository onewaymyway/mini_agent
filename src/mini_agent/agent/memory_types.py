"""agent/memory_types.py — agent/ 包内部的 MemoryEntry 访问门面。

见 `next_doc/refactor_plan/03-sprint1.5-memory-perception-coupling-assessment.md`
"三、迁移优先级建议"第 3 条 + "九、perception/memory_store.py 分层
facade · 第一层（evolution/）执行记录"末尾"遗留/下一步"：`evolution/`
层收敛后 `perception/memory_store.py` inbound 从 24 降到 19，仍超
止损阈值，需要继续推进第二层——`agent/` 包内部收敛。

复查 `agent/profile.py`/`agent/reflection.py`/
`agent/reminders_correction.py` 三个文件发现，它们与 `evolution/`
层的情况完全一致：**只用到 `MemoryEntry` 这一个类型**（构造一条
记忆条目），不涉及 `MemoryStore` 本身的读写方法——原计划里"改为
通过 Agent 对象统一访问"的说法基于"这三个文件可能持有真实
`MemoryStore` 写入需求"的预判，实际复查后该预判不成立，因此采用
与 `evolution/memory_types.py` 相同的门面模式，而不是引入更重的
"通过 Agent 对象访问"设计——这是比预想更简单的收敛，不是新建一层
抽象。

本文件只做"重新导出"（re-export），不改变 `MemoryEntry` 的定义或
行为，也不是新的领域概念——`agent/` 包内部其它文件应该从这里
import `MemoryEntry`，而不是各自直接指向
`mini_agent.perception.memory_store`。
"""

from __future__ import annotations

from mini_agent.perception.memory_store import MemoryEntry

__all__ = ["MemoryEntry"]
