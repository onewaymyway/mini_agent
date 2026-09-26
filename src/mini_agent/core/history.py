"""core/history.py — 新领域模型里的 HistorySnapshot（Experience 的
history 部分，`history_manager.py` 迁移链的最小 dataclass）。

见 `next_doc/refactor_plan/03-sprint1.5-memory-perception-coupling-assessment.md`
"六、history_manager.py facade 整理执行记录"：`history_manager.py`
经死代码清理后 inbound 深度依赖降到 2（低于 Self 迁移链的 6），
未触发止损阈值，可以直接进入 Adapter 接入点设计，参照 Self 迁移链
（`core/self.py` + `core/self_adapter.py`）的模式，不需要额外的 facade 层。

与 `core.self.SelfState` 的设计原则一致：只保留跨子系统共享、值得
被别的子系统读取的最小快照信息（active/raw history 长度、是否存在
待恢复的 snapshot），不包含 `HistoryManager` 内部实现细节
（压缩策略、raw history 的具体条目、extraction 调度状态等），这些
仍然是 `history_manager.py` 内部的事，不提前照搬进 core。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class HistorySnapshot:
    """新领域模型中的 History 状态快照（history_manager 迁移链最小版本）。"""

    active_history_len: int = 0
    raw_history_len: int = 0
    has_pending_snapshot: bool = False
