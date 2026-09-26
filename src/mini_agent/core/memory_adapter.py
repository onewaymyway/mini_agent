"""core/memory_adapter.py — MemoryAdapter：实现 `Adapter[Old, New]` 协议。

Old = `mini_agent.perception.memory_base.MemoryBackend`（接口，
      `MemoryStore`/`HybridMemoryBackend` 均实现此接口）
New = `mini_agent.core.memory.MemorySnapshot`

按 `03-sprint1.5-memory-perception-coupling-assessment.md`"十一、
perception/memory_store.py 止损口径评估 + Adapter 接入点执行记录"的
结论，`perception/memory_store.py` 的跨子系统 inbound 只有 5（低于
阈值），可以直接参照 Self/History 迁移链启动：**唯一接入点**在
`agent/core.py::Agent.__init__()` 里
`self._memory, self._global_memory = create_both_memory_backends(cfg)`
之后，对 `self._memory`（主 Agent 自己的项目级记忆，不含
`self._global_memory` 及 `evolution/failure_pattern_store.py`/
`evolution/autonomous_loop.py`/`tools/evolution.py` 里各自独立构造的
记忆实例——那些是各自子系统单独持有的记忆后端，不属于"主 Agent 自身
的记忆"这个单一接入点）做一次 `MemoryAdapter.to_new` 转换 + DEBUG
trace 记录，不改动 `MemoryStore`/`memory_factory.py` 内部逻辑。

Old 选择接口 `MemoryBackend` 而不是具体的 `MemoryStore` 类，是因为
`create_both_memory_backends()` 按配置可能返回 `MemoryStore` 或
`HybridMemoryBackend`，而两者都已实现基类具体方法 `count`——转换只
依赖这一个接口方法，不需要关心具体是哪个实现，天然比 Self/History
迁移链多了一层"面向接口"的选择，但代码形态（try/except + 单向
to_new）与两者完全一致。

只做 `entry_count`（`MemoryBackend.count`）+ `backend_kind`（具体类的
`type(old).__name__`，仅用于可观测性，不是新的领域概念）两个字段的
单向转换。`to_old` 方向当前没有实际调用方（从 `MemorySnapshot` 反向
构造一个可用的 `MemoryBackend` 需要 `AppConfig`/`library_index`/
`embed_call` 等尚未有 core 版本的依赖），先只实现 `to_new`，`to_old`
抛出 `NotImplementedError` 并注明原因，与 `core/self_adapter.py`/
`core/history_adapter.py` 的处理方式一致，不留没有说明的空实现。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .adapter import Adapter
from .memory import MemorySnapshot

if TYPE_CHECKING:
    from mini_agent.perception.memory_base import MemoryBackend


class MemoryAdapter(Adapter["MemoryBackend", MemorySnapshot]):
    """`MemoryBackend` -> `MemorySnapshot` 的转换（当前只支持这一个方向）。"""

    @staticmethod
    def to_new(old: "MemoryBackend") -> MemorySnapshot:
        return MemorySnapshot(
            entry_count=old.count,
            backend_kind=type(old).__name__,
        )

    @staticmethod
    def to_old(new: MemorySnapshot) -> "MemoryBackend":
        # TODO(memory_store 迁移链下一步)：一旦需要从 MemorySnapshot 反向
        # 构造一个可用的 MemoryBackend（例如未来某个新调用方只持有
        # core.MemorySnapshot，需要喂给仍然依赖 MemoryBackend 接口的旧
        # 代码），再实现这个方向，并补充对应的往返转换测试。当前没有
        # 任何调用方需要它，且 MemorySnapshot 本身也不足以重建一个可用
        # 的后端实例（缺 path/library_index/embed_call 等构造依赖）。
        raise NotImplementedError(
            "MemoryAdapter.to_old 尚未实现：当前 memory_store 迁移链只有"
            "MemoryBackend → MemorySnapshot 单向转换的实际调用方，见"
            "core/memory_adapter.py 模块文档字符串。"
        )
