"""Phase 10 S-A A3：默认关闭的 advisory 决策旁路测试。

见 `next_doc/refactor_plan/14-phase10-sa-item-plan.md` 第四节、第十节。
核心断言：默认关闭时零变化；开启后只发布 `DecisionMade`（`executed=False`），
**不执行所选动作**、不改变 Goal 结果；任何失败都不影响 Goal。
"""

from __future__ import annotations

import json

import pytest

from mini_agent.core import Event, EventLogStore, get_event_bus, reset_event_bus
from mini_agent.core.event_log_store import reset_event_log_subscriptions
from mini_agent.core.state_manager import reset_state_manager
from mini_agent.runtime import AgentRuntime, DecisionStageReport, run_decision_stage
from mini_agent.runtime.decision_stage import (
    STATUS_DECIDED,
    STATUS_FAILED,
    STATUS_NO_CANDIDATES,
    STATUS_SKIPPED_NO_GAP,
    STATUS_SKIPPED_NO_LLM,
    _parse_candidates,
)
from mini_agent.storage.paths import AgentPaths
from tests.test_goal_mode import FakeAgent, _FakeCfg, _confirmed_spec


@pytest.fixture(autouse=True)
def _reset():
    reset_event_bus()
    reset_state_manager()
    reset_event_log_subscriptions()
    yield
    reset_event_bus()
    reset_state_manager()
    reset_event_log_subscriptions()


class FakeLLM:
    """按 system prompt 区分三种调用（生成 / 权衡 / 选择），并记录调用次数。"""

    def __init__(self, *, generate=None, narrate=None, select=None, raise_on=None):
        self.generate = generate if generate is not None else json.dumps([
            {"type": "tool", "capability": "read_file", "arguments": {"path": "a"},
             "expected_outcome": "读到内容"},
            {"type": "workflow", "capability": "wf_check"},
        ])
        self.narrate = narrate if narrate is not None else json.dumps(
            {"possible_futures": ["可能成功"], "risks": ["可能超时"], "tradeoffs": "便宜但慢"})
        self.select = select if select is not None else json.dumps({"index": 1, "reason": "更稳"})
        self.raise_on = raise_on
        self.calls: list[str] = []

    def ask(self, prompt, *, system="", **kw):
        # 注意顺序：narrate 的 system 里也含“候选行动”，必须先判断它。
        if "评估一个候选" in system:
            kind = "narrate"
        elif "提出 2-3 个" in system:
            kind = "generate"
        else:
            kind = "select"
        self.calls.append(kind)
        if self.raise_on == kind:
            raise RuntimeError(f"boom-{kind}")
        return getattr(self, kind)


def _run(llm, gap=("gap-1", "gap-2"), tmp_path=None, **kw):
    from mini_agent.core import ExperienceStore

    store = ExperienceStore(path=tmp_path / "exp.db") if tmp_path else None
    return run_decision_stage(llm_helper=llm, goal_text="修好登录", gap=list(gap),
                              experience_store=store, **kw)


# ── 解析 ─────────────────────────────────────────────────────────────

def test_parse_candidates_drops_invalid_and_tolerates_fences():
    raw = "```json\n" + json.dumps([
        {"type": "tool", "capability": "a"},
        {"type": "bogus", "capability": "b"},      # 非法 type
        {"type": "tool", "capability": "  "},      # 空 capability
        {"type": "subagent", "capability": "c", "arguments": "notadict"},
        "notadict",
    ]) + "\n```"
    specs = _parse_candidates(raw)
    assert [(s.type, s.capability) for s in specs] == [("tool", "a"), ("subagent", "c")]
    assert specs[1].arguments == {}


def test_parse_candidates_single_element_array_and_surrounding_text():
    """回归：`[{...}]` 曾被内层对象抢先匹配，单候选输出被误判为空。"""
    one = json.dumps([{"type": "tool", "capability": "only"}])
    assert [s.capability for s in _parse_candidates(one)] == ["only"]
    assert [s.capability for s in _parse_candidates("好的：\n" + one + "\n以上。")] == ["only"]


def test_parse_candidates_accepts_object_wrapper_and_rejects_garbage():
    assert len(_parse_candidates(json.dumps({"candidates": [{"type": "tool", "capability": "x"}]}))) == 1
    with pytest.raises(ValueError):
        _parse_candidates("no json here")


# ── run_decision_stage ───────────────────────────────────────────────

def test_no_gap_makes_no_llm_calls():
    llm = FakeLLM()
    r = _run(llm, gap=())
    assert r.status == STATUS_SKIPPED_NO_GAP and llm.calls == []


def test_no_llm_helper_is_skipped_not_error():
    assert _run(None).status == STATUS_SKIPPED_NO_LLM
    assert _run(object()).status == STATUS_SKIPPED_NO_LLM  # 没有 ask 方法


def test_happy_path_decides_and_publishes_advisory_event(tmp_path):
    got: list[Event] = []
    get_event_bus().subscribe("DecisionMade", got.append)
    llm = FakeLLM()
    r = _run(llm, tmp_path=tmp_path, correlation_id="cid-1")

    assert r.status == STATUS_DECIDED and r.candidate_count == 2
    assert (r.action.type, r.action.capability) == ("workflow", "wf_check")
    # 只针对第一条 gap；调用次数 = 1 生成 + 2 权衡 + 1 选择
    assert llm.calls == ["generate", "narrate", "narrate", "select"]
    assert r.gap_item == "gap-1"

    assert len(got) == 1
    ev = got[0]
    assert ev.correlation_id == "cid-1" and ev.actor == "runtime.decision_stage"
    assert ev.payload["advisory"] is True and ev.payload["executed"] is False
    assert ev.payload["selected_capability"] == "wf_check"
    assert "更稳" in ev.payload["reason"] and "方案1" in ev.payload["trace_text"]


def test_max_candidates_bounds_llm_calls(tmp_path):
    many = json.dumps([{"type": "tool", "capability": f"t{i}"} for i in range(6)])
    llm = FakeLLM(generate=many, select=json.dumps({"index": 0, "reason": "r"}))
    r = _run(llm, tmp_path=tmp_path)
    assert r.candidate_count == 3
    assert len(llm.calls) == 1 + 3 + 1  # 文档声明的上界


def test_selected_action_is_not_executed(tmp_path, monkeypatch):
    """advisory-only：ActionExecutor 不得被调用。"""
    from mini_agent.actions import executor as ex

    called = []
    monkeypatch.setattr(ex.ActionExecutor, "execute", lambda *a, **k: called.append(1))
    _run(FakeLLM(), tmp_path=tmp_path)
    assert called == []


@pytest.mark.parametrize("stage", ["generate", "narrate", "select"])
def test_llm_failure_in_any_stage_is_contained(tmp_path, stage):
    got: list[Event] = []
    get_event_bus().subscribe("DecisionMade", got.append)
    r = _run(FakeLLM(raise_on=stage), tmp_path=tmp_path)
    assert r.status == STATUS_FAILED and f"boom-{stage}" in r.error
    assert got == []  # 失败时不发布决策事件


def test_no_valid_candidates_reports_no_candidates(tmp_path):
    r = _run(FakeLLM(generate="[]"), tmp_path=tmp_path)
    assert r.status == STATUS_NO_CANDIDATES


@pytest.mark.parametrize("bad", [
    json.dumps({"index": 9, "reason": "x"}),      # 越界
    json.dumps({"index": "1", "reason": "x"}),    # 非整数
    json.dumps({"index": True, "reason": "x"}),   # bool 不算整数
    "not json",
])
def test_bad_selection_output_fails_safely(tmp_path, bad):
    r = _run(FakeLLM(select=bad), tmp_path=tmp_path)
    assert r.status == STATUS_FAILED and r.action is None


def test_bad_narration_output_fails_safely(tmp_path):
    r = _run(FakeLLM(narrate="[1,2]"), tmp_path=tmp_path)
    assert r.status == STATUS_FAILED


def test_event_payload_text_is_clipped(tmp_path):
    got: list[Event] = []
    get_event_bus().subscribe("DecisionMade", got.append)
    huge = "x" * 5000
    llm = FakeLLM(generate=json.dumps([{"type": "tool", "capability": huge}]),
                  select=json.dumps({"index": 0, "reason": huge}))
    _run(llm, tmp_path=tmp_path)
    p = got[0].payload
    assert len(p["selected_capability"]) <= 501 and len(p["reason"]) <= 501
    assert len(p["trace_text"]) <= 2001


def test_summary_shape():
    assert DecisionStageReport(status=STATUS_SKIPPED_NO_GAP).summary() == {
        "status": STATUS_SKIPPED_NO_GAP, "candidate_count": 0}


# ── AgentRuntime 集成 ────────────────────────────────────────────────

def _patch_judge(monkeypatch):
    monkeypatch.setattr("mini_agent.role_agents.goal_judge.run_goal_judge",
                        lambda **kw: "**结论**\n全部通过\nGOAL_STATUS: DONE")


class LLMAgent(FakeAgent):
    def __init__(self, llm, **kw):
        super().__init__(**kw)
        self.llm_helper = llm


def _completed_payloads():
    out: list[dict] = []
    get_event_bus().subscribe("RuntimeCycleCompleted", lambda e: out.append(e.payload))
    return out


def test_default_off_no_llm_calls_and_payload_unchanged(monkeypatch, tmp_path):
    _patch_judge(monkeypatch)
    llm = FakeLLM()
    payloads = _completed_payloads()
    r = AgentRuntime(agent=LLMAgent(llm, outputs=["x"]), cfg=_FakeCfg(tmp_path)).run_once(_confirmed_spec())
    assert r.status == "done" and r.decision_report is None
    assert llm.calls == []
    assert "decision" not in payloads[0]


def test_opt_in_via_config_records_decision_without_changing_goal(monkeypatch, tmp_path):
    _patch_judge(monkeypatch)
    cfg = _FakeCfg(tmp_path)
    cfg.goal_mode.runtime_decision_enabled = True
    llm = FakeLLM()
    payloads = _completed_payloads()
    agent = LLMAgent(llm, outputs=["x"])
    r = AgentRuntime(agent=agent, cfg=cfg).run_once(_confirmed_spec())

    # Goal 结果与关闭时一致，且所选动作没有被执行（agent 只跑了 GoalRunner 的一轮）
    assert r.status == "done" and agent._call_idx == 1
    assert r.decision_report.status == STATUS_DECIDED
    assert r.decision_report.gap_item.startswith("未验证达成")
    assert payloads[0]["decision"]["status"] == STATUS_DECIDED
    assert payloads[0]["decision"]["selected_capability"] == "wf_check"


def test_opt_in_via_ctor_arg_overrides_config(monkeypatch, tmp_path):
    _patch_judge(monkeypatch)
    cfg = _FakeCfg(tmp_path)
    cfg.goal_mode.runtime_decision_enabled = True
    llm = FakeLLM()
    r = AgentRuntime(agent=LLMAgent(llm, outputs=["x"]), cfg=cfg,
                     enable_decision_stage=False).run_once(_confirmed_spec())
    assert r.decision_report is None and llm.calls == []


def test_decision_made_is_persisted_to_event_log(monkeypatch, tmp_path):
    """开启时应在 `GoalRunner.run()` 之前先挂载事件日志订阅，否则 DecisionMade 不落盘。"""
    _patch_judge(monkeypatch)
    cfg = _FakeCfg(tmp_path)
    cfg.goal_mode.runtime_decision_enabled = True
    r = AgentRuntime(agent=LLMAgent(FakeLLM(), outputs=["x"]), cfg=cfg).run_once(_confirmed_spec())

    log = EventLogStore(path=AgentPaths(project_root=tmp_path).workdir_event_log)
    kinds = [e["kind"] for e in log.trace(r.correlation_id)]
    assert "DecisionMade" in kinds
    # 注：RuntimeCycleStarted 在事件日志订阅之前发布，首个周期不会落盘——这是 Phase 8
    # 起的既有行为（本 Sprint 不改默认行为），所以这里不断言 Started。
    assert kinds.index("DecisionMade") < kinds.index("RuntimeCycleCompleted")


def test_agent_without_llm_helper_skips_and_goal_still_runs(monkeypatch, tmp_path):
    _patch_judge(monkeypatch)
    cfg = _FakeCfg(tmp_path)
    cfg.goal_mode.runtime_decision_enabled = True
    r = AgentRuntime(agent=FakeAgent(outputs=["x"]), cfg=cfg).run_once(_confirmed_spec())
    assert r.status == "done" and r.decision_report.status == STATUS_SKIPPED_NO_LLM


def test_llm_failure_does_not_break_goal(monkeypatch, tmp_path):
    _patch_judge(monkeypatch)
    cfg = _FakeCfg(tmp_path)
    cfg.goal_mode.runtime_decision_enabled = True
    r = AgentRuntime(agent=LLMAgent(FakeLLM(raise_on="generate"), outputs=["x"]),
                     cfg=cfg).run_once(_confirmed_spec())
    assert r.status == "done" and r.decision_report.status == STATUS_FAILED


def test_unexpected_crash_in_stage_is_contained(monkeypatch, tmp_path):
    _patch_judge(monkeypatch)
    cfg = _FakeCfg(tmp_path)
    cfg.goal_mode.runtime_decision_enabled = True
    monkeypatch.setattr("mini_agent.runtime.runtime.run_decision_stage",
                        lambda **kw: (_ for _ in ()).throw(RuntimeError("crash")))
    r = AgentRuntime(agent=LLMAgent(FakeLLM(), outputs=["x"]), cfg=cfg).run_once(_confirmed_spec())
    assert r.status == "done" and r.decision_report is None


def test_config_default_is_false():
    from mini_agent.config.models import GoalModeConfig

    assert GoalModeConfig().runtime_decision_enabled is False
