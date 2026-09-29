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

**2026-09-29 更新（`_global_memory` 纳入 + `to_old` 复核）**：

- `self._global_memory` 与 `self._memory` 由同一个 `create_both_memory_backends()`
  在同一行构造、类型同为 `MemoryBackend`，因此复用**同一个 Adapter、同一个
  接入点**，不新增任何 Adapter 类。区别只在作用域，故 `MemorySnapshot` 新增
  `scope` 字段；`MemoryBackend` 接口不携带作用域，`to_new()` 保持协议签名
  `to_new(old)` 不变，`scope` 由 `trace_memory_snapshot()` 的调用方标注。
- `to_old` 复核后**仍不实现**，理由与重评触发条件见
  `to_old` 方法内注释及 `03-sprint1.5-...md` "十二"。
"""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING

from .adapter import Adapter
from .events import Event
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
        # TODO(重评触发条件，2026-09-29 复核后仍未实现)：出现真实消费者——
        # 即某个新代码只持有 core.MemorySnapshot，却必须把它喂给仍依赖
        # MemoryBackend 接口的旧代码——再实现，并补往返转换测试。
        #
        # 复核结论（详见 03-sprint1.5-...md "十二"）：MemorySnapshot 只有
        # 条数/种类/作用域，重建一个可用后端还缺 path/library_index/
        # embed_call/AppConfig，且已持久化的记忆条目不在快照里；返回一个
        # 只读桩对象会让调用方拿到"看起来能用、实际检索不到任何东西"的
        # 后端，比显式报错更危险。因此保持 NotImplementedError。
        raise NotImplementedError(
            "MemoryAdapter.to_old 尚未实现：当前 memory_store 迁移链只有"
            "MemoryBackend → MemorySnapshot 单向转换的实际调用方，见"
            "core/memory_adapter.py 模块文档字符串。"
        )


_TRACE_LOGGER_NAME = "mini_agent.core.trace"


def trace_memory_snapshot(old: "MemoryBackend", scope: str) -> MemorySnapshot:
    """把一个记忆后端转换成 `MemorySnapshot` 并写一条 DEBUG trace。

    `agent/core.py::Agent.__init__()` 唯一接入点对 `self._memory`
    （`scope="project"`）与 `self._global_memory`（`scope="global"`）
    各调用一次。抽成函数而不是在接入点内联，是为了让测试直接执行真实的
    转换 + 日志代码，而不是复刻一份（`tests/test_core_history_adapter.py`
    的 trace 测试就是复刻式的，覆盖不到接入点真正执行的那段代码）。

    纯旁路：只读 `old.count`，不修改后端；异常向调用方传播，由接入点
    统一 `log_exception` 兜底（与原有行为一致）。
    """
    snapshot = dataclasses.replace(MemoryAdapter.to_new(old), scope=scope)
    logging.getLogger(_TRACE_LOGGER_NAME).debug(
        "%s",
        Event(
            kind="memory_store.adapter.to_new",
            payload={
                "entry_count": snapshot.entry_count,
                "backend_kind": snapshot.backend_kind,
                "scope": snapshot.scope,
            },
        ).to_dict(),
    )
    return snapshot
