"""tests/test_phase3_experience_retrieval_injection.py

对应 Sprint 3-2 验收标准第 2 条：“下一个类似 Goal 执行时，Retriever 能
检索到它，并且这个检索结果真实出现在了传给 LLM 的 context 里（不是
‘检索到了但没用上’）”。

复用 `tests/test_goal_mode.py` 里已有的 `FakeAgent`/`_FakeCfg`/
`_confirmed_spec` 测试替身（与 `test_goal_mode_phase2_events.py` 的
既有做法一致）。
"""

from __future__ import annotations

import pytest

from mini_agent.core import Experience, ExperienceStore, get_event_bus, reset_event_bus
from mini_agent.core.experience_recorder import reset_experience_recorder_subscriptions
from mini_agent.goal_mode.runner import GoalRunner
from mini_agent.storage.paths import AgentPaths
from tests.test_goal_mode import FakeAgent, _FakeCfg, _confirmed_spec


@pytest.fixture(autouse=True)
def _reset_bus_and_recorder():
    reset_event_bus()
    reset_experience_recorder_subscriptions()
    yield
    reset_event_bus()
    reset_experience_recorder_subscriptions()


def test_experience_retrieval_disabled_by_default_injects_nothing(monkeypatch, tmp_path):
    """默认配置（`experience_retrieval_enabled` 未开启）下，历史上下文
    不会被注入——对应"保守 opt-in 默认值"，验证 Sprint 3-2 新增功能
    不影响未开启该开关的既有用户。"""
    agent = FakeAgent(outputs=["did the thing"])
    cfg = _FakeCfg(tmp_path)
    spec = _confirmed_spec()

    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge",
        lambda **kw: "**结论**\n全部通过\nGOAL_STATUS: DONE",
    )

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    runner.run()

    injected = [
        e for e in agent._hist.entries
        if isinstance(e, dict) and e.get("_type") == "goal_experience_context"
    ]
    assert injected == []


def test_experience_retrieval_enabled_injects_similar_past_experience(monkeypatch, tmp_path):
    """开启开关 + 已有一条相似历史 Experience 时，检索结果必须真实出现在
    `agent._hist` 里（即将被拼进传给 LLM 的 context）。"""
    # 预先在这次 run() 将要读取的同一个 store 路径写入一条相似历史记录。
    paths = AgentPaths(project_root=tmp_path)
    store = ExperienceStore(path=paths.workdir_experience_store)
    store.append(
        Experience(
            source="goal_mode",
            goal_text="do the thing yesterday",
            status="stuck",
            rounds_used=5,
            final_report="ran into a blocker",
            lesson="check permissions first",
        )
    )

    agent = FakeAgent(outputs=["did the thing"])
    cfg = _FakeCfg(tmp_path)
    cfg.goal_mode.experience_retrieval_enabled = True
    cfg.goal_mode.experience_retrieval_limit = 3
    spec = _confirmed_spec()  # goal_text="do the thing"

    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge",
        lambda **kw: "**结论**\n全部通过\nGOAL_STATUS: DONE",
    )

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    runner.run()

    injected = [
        e for e in agent._hist.entries
        if isinstance(e, dict) and e.get("_type") == "goal_experience_context"
    ]
    assert len(injected) == 1
    assert "do the thing yesterday" in injected[0]["content"]
    assert "check permissions first" in injected[0]["content"]


def test_experience_retrieval_failure_does_not_break_run(monkeypatch, tmp_path):
    """检索环节抛异常时，不应该影响 Goal 本身的执行结果（纯旁路兜底）。"""
    agent = FakeAgent(outputs=["did the thing"])
    cfg = _FakeCfg(tmp_path)
    cfg.goal_mode.experience_retrieval_enabled = True
    spec = _confirmed_spec()

    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge",
        lambda **kw: "**结论**\n全部通过\nGOAL_STATUS: DONE",
    )
    monkeypatch.setattr(
        "mini_agent.core.retrieve_similar_experiences",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    result = runner.run()

    assert result.status == "done"
