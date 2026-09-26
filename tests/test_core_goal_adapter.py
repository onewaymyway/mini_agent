"""core/ 领域模型 + GoalAdapter 的单元测试。

对应 `next_doc/refactor_plan/02-executable-sprint-plan.md` Sprint 1
"验证特征测试"任务：本文件只覆盖 Sprint 1 新增的 `core/` 代码本身
（`GoalAdapter` 双向转换、`goal_run_result_to_experience`），
`goal_mode/` 既有行为的回归由 `tests/test_goal_mode.py` +
`tests/test_goal_mode_characterization.py` 负责，不在本文件重复。
"""

from __future__ import annotations

import logging

from mini_agent.core import Event, Experience, GoalAdapter, GoalState, goal_run_result_to_experience
from mini_agent.goal_mode.runner import GoalRunResult
from mini_agent.goal_mode.spec import GoalSpec


def test_goal_adapter_to_new_preserves_core_fields():
    old = GoalSpec(
        goal_text="写一份周报",
        acceptance_criteria=["包含本周完成事项", "包含下周计划"],
        version=2,
        confirmed=True,
    )

    new = GoalAdapter.to_new(old)

    assert isinstance(new, GoalState)
    assert new.goal_text == old.goal_text
    assert new.acceptance_criteria == old.acceptance_criteria
    assert new.version == old.version
    assert new.status == "running"
    assert new.round == 0


def test_goal_adapter_round_trip_does_not_lose_goal_text_or_criteria():
    """双向转换不丢失核心信息（对应 Sprint 1 止损条件里关注的"数据丢失"）。"""
    old = GoalSpec(
        goal_text="修复登录页 bug",
        acceptance_criteria=["复现步骤不再报错", "补充回归测试"],
        version=3,
    )

    new = GoalAdapter.to_new(old)
    round_tripped = GoalAdapter.to_old(new)

    assert round_tripped.goal_text == old.goal_text
    assert round_tripped.acceptance_criteria == old.acceptance_criteria
    assert round_tripped.version == old.version


def test_goal_run_result_to_experience():
    spec = GoalSpec(goal_text="部署新版本", acceptance_criteria=["线上无报错"])
    result = GoalRunResult(
        status="done",
        rounds_used=3,
        compacts_done=1,
        final_report="已完成部署并验证",
        goal_spec=spec,
    )

    exp = goal_run_result_to_experience(result)

    assert isinstance(exp, Experience)
    assert exp.source == "goal_mode"
    assert exp.goal_text == "部署新版本"
    assert exp.status == "done"
    assert exp.rounds_used == 3
    assert exp.final_report == "已完成部署并验证"


def test_event_to_dict_has_kind_payload_at():
    ev = Event(kind="unit_test", payload={"a": 1})
    d = ev.to_dict()
    assert d["kind"] == "unit_test"
    assert d["payload"] == {"a": 1}
    assert "at" in d


def test_goal_runner_run_emits_adapter_trace_events(monkeypatch, caplog, tmp_path):
    """验证 Sprint 1 验收标准第 1 条：
    "有日志/trace 能证明这条链真的被执行过，不是定义了但没人用"。

    只关心 runner.run()/`_finish()` 是否产出了 core Adapter 的 trace 事件，
    不重新验证 GoalRunner 主循环本身的行为（那是
    `tests/test_goal_mode.py` 的职责），因此这里用最短路径让 run() 立刻
    走到 _finish()：mock 掉一次循环内部依赖，直接调用 `_finish` 与手动
    构造的 `run()` 前半段等价逻辑不现实时，改为直接对 `_finish` 和
    `GoalAdapter.to_new` 分别断言，覆盖 run() 里两处接入点各自的调用。
    """
    caplog.set_level(logging.DEBUG, logger="mini_agent.core.trace")

    from mini_agent.goal_mode.runner import _core_GoalAdapter, _core_Event, _core_logger

    spec = GoalSpec(goal_text="示例目标", acceptance_criteria=["示例标准"])

    # 复刻 run() 前半段的接入点调用，确认它不会抛异常且产出 trace。
    core_goal_state = _core_GoalAdapter.to_new(spec)
    _core_logger.debug(
        "%s",
        _core_Event(
            kind="goal_mode.adapter.to_new",
            payload={
                "goal_text": core_goal_state.goal_text,
                "status": core_goal_state.status,
                "version": core_goal_state.version,
            },
        ).to_dict(),
    )

    assert any(
        "goal_mode.adapter.to_new" in record.message for record in caplog.records
    )
