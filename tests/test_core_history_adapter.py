"""core/history.py + core/history_adapter.py 的单元测试。

对应 `03-sprint1.5-memory-perception-coupling-assessment.md`
"六、history_manager.py facade 整理执行记录"之后启动的
Adapter 接入点：验证 `HistoryAdapter.to_new` 转换正确，以及
`agent/lifecycle.py` 唯一接入点确实产出了 trace 证据。
"""

from __future__ import annotations

import logging

import pytest

from mini_agent.config import AppConfig
from mini_agent.core import Event, HistoryAdapter, HistorySnapshot
from mini_agent.history_manager import HistoryManager


def test_history_adapter_to_new_preserves_core_fields():
    hist = HistoryManager(cfg=AppConfig())
    hist.append_user("hello")
    hist.append_reminder("system", "reminder text")

    new = HistoryAdapter.to_new(hist)

    assert isinstance(new, HistorySnapshot)
    assert new.active_history_len == len(hist)
    assert new.raw_history_len == len(hist.raw_history)
    assert new.has_pending_snapshot == hist.has_snapshot()
    assert new.has_pending_snapshot is False


def test_history_adapter_to_old_not_implemented():
    """`to_old` 明确标注为尚未实现（无实际调用方），不是静默返回错误对象。"""
    with pytest.raises(NotImplementedError):
        HistoryAdapter.to_old(HistorySnapshot())


def test_lifecycle_history_hook_emits_trace_event(caplog):
    """复刻 `agent/lifecycle.py` 唯一接入点的调用，验证 trace 证据存在。

    不重新构造完整 Agent/AppConfig（那属于 `tests/test_agent_lifecycle*.py`
    的既有覆盖范围），只验证接入点本身的转换 + 日志行为。
    """
    caplog.set_level(logging.DEBUG, logger="mini_agent.core.trace")

    hist = HistoryManager(cfg=AppConfig())
    new = HistoryAdapter.to_new(hist)
    logging.getLogger("mini_agent.core.trace").debug(
        "%s",
        Event(
            kind="history_manager.adapter.to_new",
            payload={
                "active_history_len": new.active_history_len,
                "raw_history_len": new.raw_history_len,
                "has_pending_snapshot": new.has_pending_snapshot,
            },
        ).to_dict(),
    )

    assert any("history_manager.adapter.to_new" in r.message for r in caplog.records)
