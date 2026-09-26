"""core/memory.py — 新领域模型里的 MemorySnapshot（Memory 迁移链的最小
dataclass）。

见 `next_doc/refactor_plan/03-sprint1.5-memory-perception-coupling-assessment.md`
"十一、perception/memory_store.py 止损口径评估 + Adapter 接入点执行
记录"：按 `12-execution-and-doc-sync-norms.md` 第六节新增的"同包内部
调用不计入跨子系统止损阈值"口径，`perception/memory_store.py` 的
**跨子系统** inbound 只有 5（远低于阈值 10+），可以参照 Self/History
迁移链的模式直接进入 Adapter 接入点设计，不需要再等第三层 facade。

与 `core.self.SelfState`/`core.history.HistorySnapshot` 的设计原则
一致：只保留跨子系统共享、值得被别的子系统读取的最小快照信息（当前
记忆条数、后端实现种类），不包含 `MemoryStore`/`HybridMemoryBackend`
内部实现细节（TF-IDF 检索、embedding 向量、归纳巩固策略等），这些
仍然是各自后端内部的事，不提前照搬进 core。

`entry_count` 取自 `MemoryBackend.count` —— 这是基类上的具体方法
（非抽象方法各自实现细节），`MemoryStore`/`HybridMemoryBackend` 都
已覆盖实现，因此 Old 类型是接口 `MemoryBackend` 而不是某个具体子类，
比 Self/History 迁移链多了一层"面向接口而非面向实现"的选择——见
`core/memory_adapter.py` 模块文档字符串。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class MemorySnapshot:
    """新领域模型中的 Memory 状态快照（memory_store 迁移链最小版本）。"""

    entry_count: int = 0
    backend_kind: str = "unknown"
