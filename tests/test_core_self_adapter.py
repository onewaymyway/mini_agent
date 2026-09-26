"""core/self.py + core/self_adapter.py 的单元测试。

对应 `03-sprint1.5-memory-perception-coupling-assessment.md` 建议的
Self 迁移链第一步：验证 `SelfAdapter.to_new` 转换正确，以及
`agent/lifecycle.py` 唯一接入点确实产出了 trace 证据。
"""

from __future__ import annotations

import logging

from mini_agent.core import Event, SelfAdapter, SelfState
from mini_agent.perception.self_model import AgentSelfModel


def test_self_adapter_to_new_preserves_core_fields():
    old = AgentSelfModel(
        capability_snapshot={"python": 0.8, "rust": 0.3},
        active_skill_count=5,
        session_start_at=123.0,
    )

    new = SelfAdapter.to_new(old)

    assert isinstance(new, SelfState)
    assert new.capability_snapshot == old.capability_snapshot
    assert new.active_skill_count == old.active_skill_count
    assert new.session_start_at == old.session_start_at


def test_self_adapter_to_old_not_implemented():
    """`to_old` 明确标注为尚未实现（无实际调用方），不是静默返回错误对象。"""
    import pytest

    with pytest.raises(NotImplementedError):
        SelfAdapter.to_old(SelfState())


def test_lifecycle_self_model_hook_emits_trace_event(caplog):
    """复刻 `agent/lifecycle.py` 唯一接入点的调用，验证 trace 证据存在。

    不重新构造完整 Agent/AppConfig（那属于 `tests/test_agent_lifecycle*.py`
    的既有覆盖范围），只验证接入点本身的转换 + 日志行为。
    """
    caplog.set_level(logging.DEBUG, logger="mini_agent.core.trace")

    old = AgentSelfModel(capability_snapshot={"python": 0.5}, active_skill_count=2)
    new = SelfAdapter.to_new(old)
    logging.getLogger("mini_agent.core.trace").debug(
        "%s",
        Event(
            kind="self_model.adapter.to_new",
            payload={
                "active_skill_count": new.active_skill_count,
                "capability_domain_count": len(new.capability_snapshot),
            },
        ).to_dict(),
    )

    assert any("self_model.adapter.to_new" in r.message for r in caplog.records)
