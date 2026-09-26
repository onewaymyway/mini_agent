"""core/history_adapter.py — HistoryAdapter：实现 `Adapter[Old, New]` 协议。

Old = `mini_agent.history_manager.HistoryManager`
New = `mini_agent.core.history.HistorySnapshot`

按 `03-sprint1.5-memory-perception-coupling-assessment.md`"六、
history_manager.py facade 整理执行记录"的结论，`history_manager.py`
死代码清理后 inbound=2，可以直接参照 Self 迁移链的模式启动：**唯一
接入点**在 `agent/lifecycle.py::_init_components()` 里
`self._hist = HistoryManager(...)` 构造之后，做一次
`HistoryAdapter.to_new` 转换 + DEBUG trace 记录，不改动
`history_manager.py` 内部逻辑。

只做 `active_history_len`/`raw_history_len`/`has_pending_snapshot`
三个字段的单向转换（`HistoryManager → HistorySnapshot`）。`to_old`
方向当前没有实际调用方（`HistoryManager` 构造需要 `AppConfig`/
`SkillLoader` 等尚未有 core 版本的依赖），先只实现 `to_new`，`to_old`
抛出 `NotImplementedError` 并注明原因，与 `core/self_adapter.py` 的
处理方式一致，不留没有说明的空实现。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .adapter import Adapter
from .history import HistorySnapshot

if TYPE_CHECKING:
    from mini_agent.history_manager import HistoryManager


class HistoryAdapter(Adapter["HistoryManager", HistorySnapshot]):
    """`HistoryManager` -> `HistorySnapshot` 的转换（当前只支持这一个方向）。"""

    @staticmethod
    def to_new(old: "HistoryManager") -> HistorySnapshot:
        return HistorySnapshot(
            active_history_len=len(old),
            raw_history_len=len(old.raw_history),
            has_pending_snapshot=old.has_snapshot(),
        )

    @staticmethod
    def to_old(new: HistorySnapshot) -> "HistoryManager":
        # TODO(history_manager 迁移链下一步)：一旦需要从 HistorySnapshot
        # 反向构造 HistoryManager（例如未来某个新调用方只持有
        # core.HistorySnapshot，需要喂给仍然依赖 HistoryManager 的旧
        # 代码），再实现这个方向，并补充对应的往返转换测试。当前没有
        # 任何调用方需要它。
        raise NotImplementedError(
            "HistoryAdapter.to_old 尚未实现：当前 history_manager 迁移链"
            "只有 HistoryManager → HistorySnapshot 单向转换的实际调用方，"
            "见 core/history_adapter.py 模块文档字符串。"
        )
