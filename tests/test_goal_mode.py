"""
tests/test_goal_mode.py — Goal 模式核心逻辑单元测试

覆盖范围：
  - GoalSpec 序列化/渲染
  - GoalSpecBuilder 的 JSON 提取与 diff 展示（纯函数部分，不走真实 LLM）
  - GoalStateStore 落盘/恢复/清理 + find_resumable_session 扫描
  - GoalRunner 主循环：DONE / CONTINUE / NEED_COMPACT / max_turns 兜底 /
    连续雷同反馈提前终止 / max_rounds 耗尽

GoalRunner 测试通过一个轻量 FakeAgent + monkeypatch run_goal_judge 来隔离真实
LLM 调用，只验证 GoalRunner 自身的状态机逻辑。
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from mini_agent.goal_mode.spec import (
    GoalSpec,
    GoalSpecBuilder,
    _extract_json,
    _extract_history_transcript,
)
from mini_agent.goal_mode.state import GoalState, GoalStateStore, find_resumable_session
from mini_agent.goal_mode.executor import GoalStepResult, GoalStepExecutor
from mini_agent.goal_mode.runner import GoalRunner
from mini_agent.storage.paths import AgentPaths
from mini_agent.role_agents.feedback import extract_goal_status, RoleFeedback, format_feedback


# ── GoalSpec ─────────────────────────────────────────────────────────────

def test_goal_spec_roundtrip():
    spec = GoalSpec(goal_text="fix bug", acceptance_criteria=["a", "b"], version=2, confirmed=True)
    d = spec.to_dict()
    spec2 = GoalSpec.from_dict(d)
    assert spec2.goal_text == "fix bug"
    assert spec2.acceptance_criteria == ["a", "b"]
    assert spec2.version == 2
    assert spec2.confirmed is True


def test_goal_spec_render_context_block_contains_criteria():
    spec = GoalSpec(goal_text="goal", acceptance_criteria=["c1", "c2"])
    block = spec.render_context_block()
    assert "goal" in block
    assert "c1" in block and "c2" in block


# ── GoalSpecBuilder 纯函数部分 ────────────────────────────────────────────

def test_extract_json_from_markdown_fence():
    raw = "some text\n```json\n{\"goal_text\": \"x\", \"acceptance_criteria\": [\"y\"]}\n```\n"
    d = _extract_json(raw)
    assert d == {"goal_text": "x", "acceptance_criteria": ["y"]}


def test_extract_json_from_bare_braces():
    raw = 'noise {"goal_text": "x", "acceptance_criteria": []} trailing'
    d = _extract_json(raw)
    assert d["goal_text"] == "x"


def test_extract_json_returns_none_on_garbage():
    assert _extract_json("no json here at all") is None


def test_diff_summary_reports_added_and_removed():
    builder = GoalSpecBuilder.__new__(GoalSpecBuilder)
    old = GoalSpec(goal_text="a", acceptance_criteria=["c1", "c2"], version=1)
    new = GoalSpec(goal_text="a2", acceptance_criteria=["c1", "c3"], version=2)
    summary = builder.diff_summary(old, new)
    assert "c3" in summary and "新增" in summary
    assert "c2" in summary and "移除" in summary
    assert "a2" in summary


# ── build_from_history / _extract_history_transcript ───────────────────

def test_extract_history_transcript_filters_non_text_roles():
    history = [
        {"role": "system", "content": "system prompt, should be ignored"},
        {"role": "user", "content": "帮我修一下这个 bug"},
        {"role": "tool", "content": "tool result, should be ignored"},
        {"role": "assistant", "content": "好的，已定位到问题"},
    ]
    transcript, truncated = _extract_history_transcript(history)
    assert "帮我修一下这个 bug" in transcript
    assert "已定位到问题" in transcript
    assert "should be ignored" not in transcript
    assert truncated is False


def test_extract_history_transcript_extracts_text_blocks_from_list_content():
    history = [
        {"role": "user", "content": [{"type": "text", "text": "看看这个"}, {"type": "image", "url": "x"}]},
    ]
    transcript, truncated = _extract_history_transcript(history)
    assert "看看这个" in transcript
    assert truncated is False


def test_extract_history_transcript_truncates_by_message_count():
    history = [{"role": "user", "content": f"msg-{i}"} for i in range(50)]
    transcript, truncated = _extract_history_transcript(history, max_messages=10, max_chars=100000)
    assert truncated is True
    assert "msg-49" in transcript
    assert "msg-0" not in transcript


def test_extract_history_transcript_empty_history_returns_empty():
    transcript, truncated = _extract_history_transcript([])
    assert transcript == ""
    assert truncated is False


def test_build_from_history_returns_empty_spec_when_no_text_history():
    builder = GoalSpecBuilder.__new__(GoalSpecBuilder)
    spec = builder.build_from_history([{"role": "tool", "content": "irrelevant"}])
    assert spec.goal_text == ""
    assert spec.acceptance_criteria == []


def test_build_from_history_parses_llm_output(monkeypatch):
    builder = GoalSpecBuilder.__new__(GoalSpecBuilder)
    monkeypatch.setattr(
        builder,
        "_run_builder",
        lambda prompt: '{"goal_text": "修复多 session 消息丢失问题", '
        '"acceptance_criteria": ["c1", "c2"], '
        '"verification_method": "manual_review", "verification_command": ""}',
    )
    history = [
        {"role": "user", "content": "Client B 收不到回复"},
        {"role": "assistant", "content": "定位到是事件循环没绑定"},
    ]
    spec = builder.build_from_history(history)
    assert spec.goal_text == "修复多 session 消息丢失问题"
    assert spec.acceptance_criteria == ["c1", "c2"]
    assert spec.negotiation_log[-1]["source"] == "builder_from_history"


def test_build_from_history_fallback_criteria_when_missing(monkeypatch):
    builder = GoalSpecBuilder.__new__(GoalSpecBuilder)
    monkeypatch.setattr(
        builder,
        "_run_builder",
        lambda prompt: '{"goal_text": "some inferred goal", "acceptance_criteria": []}',
    )
    history = [{"role": "user", "content": "do something"}]
    spec = builder.build_from_history(history)
    assert spec.goal_text == "some inferred goal"
    assert len(spec.acceptance_criteria) > 0


# ── GoalStateStore ───────────────────────────────────────────────────────

def test_goal_state_store_save_load_roundtrip(tmp_path):
    paths = AgentPaths(project_root=tmp_path)
    store = GoalStateStore(paths, "sess-1")

    assert store.load() is None
    assert not store.exists()

    state = GoalState(status="running", session_id="sess-1", round=3)
    store.save(state)

    assert store.exists()
    loaded = store.load()
    assert loaded.status == "running"
    assert loaded.round == 3

    store.clear()
    assert not store.exists()
    assert store.load() is None


def test_goal_state_store_corrupted_file_returns_none(tmp_path):
    paths = AgentPaths(project_root=tmp_path)
    store = GoalStateStore(paths, "sess-2")
    path = store._path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not valid json", encoding="utf-8")
    assert store.load() is None


def test_find_resumable_session_picks_running_status(tmp_path):
    paths = AgentPaths(project_root=tmp_path)

    store_done = GoalStateStore(paths, "sess-done")
    store_done.save(GoalState(status="done", session_id="sess-done"))

    store_running = GoalStateStore(paths, "sess-running")
    store_running.save(GoalState(status="running", session_id="sess-running"))

    sid = find_resumable_session(tmp_path)
    assert sid == "sess-running"


def test_find_resumable_session_none_when_no_sessions(tmp_path):
    assert find_resumable_session(tmp_path) is None


def test_goal_judge_yes_mode_disables_sandbox(monkeypatch, tmp_path):
    """judge_tools_enabled=True 且 judge_yes_mode=True 时，GoalJudge 的工具调用
    应该真实执行（sandbox=False + auto_approve=True），而不是被 sandbox 拦截
    成"would have executed"。"""
    import mini_agent.agent as agent_mod
    from mini_agent.config.loader import load_config
    from mini_agent.orchestrator.agent_profiles import AgentProfile
    from mini_agent.role_agents.goal_judge import run_goal_judge

    captured = {}

    class FakeInnerAgent:
        def __init__(self, cfg, guard, registry, **kwargs):
            captured["cfg_sandbox"] = cfg.sandbox
            captured["guard_sandbox"] = guard.sandbox
            captured["guard_auto_approve"] = guard.auto_approve

        def run_turn(self, prompt):
            return "GOAL_STATUS: DONE"

    monkeypatch.setattr(agent_mod, "Agent", FakeInnerAgent)

    base_cfg = load_config(
        project_root=tmp_path, verbose=False, sandbox=True,
        auto_approve=True, model="claude-sonnet-4-6",
    )
    base_cfg.api_key = "sk-fake"
    base_cfg.goal_mode.judge_tools_enabled = True
    base_cfg.goal_mode.judge_yes_mode = True

    profile = AgentProfile(name="goal_judge", role_type="goal_judge")
    spec = GoalSpec(goal_text="g", acceptance_criteria=["c"], confirmed=True)
    run_goal_judge(profile, base_cfg, spec, "output", 1, "")

    assert captured["guard_sandbox"] is False
    assert captured["guard_auto_approve"] is True
    assert captured["cfg_sandbox"] is False


def test_goal_judge_tools_enabled_without_yes_mode_keeps_sandbox(monkeypatch, tmp_path):
    """judge_tools_enabled=True 但 judge_yes_mode 默认 False 时，仍然强制
    sandbox=True（工具调用会被拦截，只显示 would-have-executed），行为不变。"""
    import mini_agent.agent as agent_mod
    from mini_agent.config.loader import load_config
    from mini_agent.orchestrator.agent_profiles import AgentProfile
    from mini_agent.role_agents.goal_judge import run_goal_judge

    captured = {}

    class FakeInnerAgent:
        def __init__(self, cfg, guard, registry, **kwargs):
            captured["guard_sandbox"] = guard.sandbox

        def run_turn(self, prompt):
            return "GOAL_STATUS: DONE"

    monkeypatch.setattr(agent_mod, "Agent", FakeInnerAgent)

    base_cfg = load_config(
        project_root=tmp_path, verbose=False, sandbox=True,
        auto_approve=True, model="claude-sonnet-4-6",
    )
    base_cfg.api_key = "sk-fake"
    base_cfg.goal_mode.judge_tools_enabled = True
    # judge_yes_mode 保持默认 False

    profile = AgentProfile(name="goal_judge", role_type="goal_judge")
    spec = GoalSpec(goal_text="g", acceptance_criteria=["c"], confirmed=True)
    run_goal_judge(profile, base_cfg, spec, "output", 1, "")

    assert captured["guard_sandbox"] is True


def test_goal_judge_prompt_loaded_via_prompt_manager():
    """GoalJudge 的 system prompt 必须来自 prompts/system/goal_judge.md，
    不能硬编码在 Python 代码里（不应该再有 DEFAULT_GOAL_JUDGE_SYSTEM 常量）。"""
    import mini_agent.role_agents.goal_judge as gj_mod
    from mini_agent.prompts import pm

    assert not hasattr(gj_mod, "DEFAULT_GOAL_JUDGE_SYSTEM"), (
        "system prompt 不应再硬编码为模块级常量，应通过 pm.render('system/goal_judge') 加载"
    )
    rendered = pm.render("system/goal_judge")
    assert "GOAL_STATUS" in rendered
    assert not rendered.startswith("#")  # 确认注释头没有残留


def test_goal_spec_builder_prompt_loaded_via_prompt_manager():
    """GoalSpecBuilder 的 system prompt 同理必须来自
    prompts/system/goal_spec_builder.md。"""
    import mini_agent.goal_mode.spec as spec_mod
    from mini_agent.prompts import pm

    assert not hasattr(spec_mod, "DEFAULT_SPEC_BUILDER_SYSTEM"), (
        "system prompt 不应再硬编码为模块级常量，应通过 "
        "pm.render('system/goal_spec_builder') 加载"
    )
    rendered = pm.render("system/goal_spec_builder")
    assert "acceptance_criteria" in rendered
    assert not rendered.startswith("#")


def test_goal_judge_uses_distinct_agent_name(monkeypatch, tmp_path):
    """GoalJudge 内部 Agent 必须用专属的 agent_name，不能沿用主 Agent 的名字，
    否则打印出来会看起来像主 Agent 自己在说话，分不清是评估者的输出。"""
    import mini_agent.agent as agent_mod
    from mini_agent.config.loader import load_config
    from mini_agent.orchestrator.agent_profiles import AgentProfile
    from mini_agent.role_agents.goal_judge import run_goal_judge

    captured = {}

    class FakeInnerAgent:
        def __init__(self, cfg, guard, registry, **kwargs):
            captured["cfg"] = cfg

        def run_turn(self, prompt):
            return "GOAL_STATUS: DONE"

    monkeypatch.setattr(agent_mod, "Agent", FakeInnerAgent)

    base_cfg = load_config(
        project_root=tmp_path, verbose=False, sandbox=True,
        auto_approve=True, model="claude-sonnet-4-6",
    )
    base_cfg.api_key = "sk-fake"

    profile = AgentProfile(name="goal_judge", role_type="goal_judge")
    spec = GoalSpec(goal_text="g", acceptance_criteria=["c"], confirmed=True)
    run_goal_judge(profile, base_cfg, spec, "output", 1, "")

    assert captured["cfg"].agent_name != base_cfg.agent_name
    assert "GoalJudge" in captured["cfg"].agent_name


def test_goal_spec_builder_uses_distinct_agent_name(monkeypatch, tmp_path):
    """GoalSpecBuilder 同理，也要用专属 agent_name。"""
    import mini_agent.agent as agent_mod
    from mini_agent.config.loader import load_config

    captured = {}

    class FakeInnerAgent:
        def __init__(self, cfg, guard, registry, **kwargs):
            captured["cfg"] = cfg

        def run_turn(self, prompt):
            return '{"goal_text": "g", "acceptance_criteria": ["c"]}'

    monkeypatch.setattr(agent_mod, "Agent", FakeInnerAgent)

    base_cfg = load_config(
        project_root=tmp_path, verbose=False, sandbox=True,
        auto_approve=True, model="claude-sonnet-4-6",
    )
    base_cfg.api_key = "sk-fake"

    builder = GoalSpecBuilder(base_cfg)
    builder.build_initial("do the thing")

    assert captured["cfg"].agent_name != base_cfg.agent_name
    assert "GoalSpecBuilder" in captured["cfg"].agent_name


def test_scan_goal_states_reports_non_running_records(tmp_path):
    from mini_agent.goal_mode.state import scan_goal_states

    paths = AgentPaths(project_root=tmp_path)
    GoalStateStore(paths, "sess-a").save(GoalState(status="done", session_id="sess-a", round=5))
    GoalStateStore(paths, "sess-b").save(GoalState(status="cancelled", session_id="sess-b"))

    records = scan_goal_states(tmp_path)
    statuses = {r["session_id"]: r["status"] for r in records}
    assert statuses == {"sess-a": "done", "sess-b": "cancelled"}
    assert find_resumable_session(tmp_path) is None


def test_scan_goal_states_empty_when_no_sessions_dir(tmp_path):
    from mini_agent.goal_mode.state import scan_goal_states
    assert scan_goal_states(tmp_path) == []


def test_scan_goal_states_reports_corrupted_file(tmp_path):
    from mini_agent.goal_mode.state import scan_goal_states

    paths = AgentPaths(project_root=tmp_path)
    store = GoalStateStore(paths, "sess-corrupt")
    store._path.parent.mkdir(parents=True, exist_ok=True)
    store._path.write_text("{broken", encoding="utf-8")

    records = scan_goal_states(tmp_path)
    assert len(records) == 1
    assert records[0]["session_id"] == "sess-corrupt"
    assert records[0]["error"] is not None


# ── feedback.extract_goal_status ─────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("blah\nGOAL_STATUS: DONE\n", "DONE"),
    ("blah\nGOAL_STATUS: continue\n", "CONTINUE"),
    ("GOAL_STATUS:NEED_COMPACT", "NEED_COMPACT"),
    ("no status keyword here", None),
])
def test_extract_goal_status(text, expected):
    assert extract_goal_status(text) == expected


def test_format_feedback_goal_judge_shows_status():
    fb = RoleFeedback(role_name="goal_judge", role_type="goal_judge",
                       raw_output="details here", goal_status="DONE")
    rendered = format_feedback(fb)
    assert "目标核查" in rendered
    assert "已达成" in rendered


def test_compat_make_goal_context_uses_real_impl_when_available():
    from mini_agent.goal_mode._compat import make_goal_context
    d = make_goal_context("hello")
    assert d["content"] == "hello"
    assert d["role"] == "user"
    # 有真实 entry.py 实现时应该拿到 HType 枚举值（字符串值仍是 goal_context）
    assert str(d["_type"]) == "HType.GOAL_CONTEXT" or d["_type"] == "goal_context"


def test_compat_make_goal_context_falls_back_when_entry_lacks_it(monkeypatch):
    import mini_agent.goal_mode._compat as compat_mod
    import builtins

    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "mini_agent.history.entry":
            raise ImportError("simulated: entry.py 缺少 make_goal_context")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    d = compat_mod.make_goal_context("fallback content")
    assert d == {"role": "user", "content": "fallback content", "_type": "goal_context"}


# ── GoalRunner（用 FakeAgent + monkeypatch run_goal_judge 隔离真实 LLM）──

class _FakeStats:
    def __init__(self):
        self.turns = 0
        self.tool_calls = 0


class _FakeHist:
    def __init__(self):
        self.entries = []

    def append_raw_dict(self, d):
        self.entries.append(d)


class FakeAgent:
    """最小可用的 Agent 替身：只实现 GoalRunner/CoarseStepExecutor 需要的接口。"""

    def __init__(self, outputs, hit_max_turns_flags=None):
        self.session_id = "fake-session"
        self.stats = _FakeStats()
        self._hist = _FakeHist()
        self._outputs = list(outputs)
        self._hit_flags = list(hit_max_turns_flags or [False] * len(outputs))
        self._call_idx = 0
        self.last_turn_hit_max_turns = False
        self.compact_calls = 0

    def run_turn(self, prompt):
        idx = self._call_idx
        self._call_idx += 1
        self.stats.turns += 1
        self.stats.tool_calls += 1
        self.last_turn_hit_max_turns = self._hit_flags[idx]
        return self._outputs[idx]

    def compact_with_skills(self):
        self.compact_calls += 1
        return f"[fake summary #{self.compact_calls}]"


def _confirmed_spec():
    return GoalSpec(goal_text="do the thing", acceptance_criteria=["it works"], confirmed=True)


class _FakeGoalModeCfg:
    def __init__(self, **kwargs):
        self.max_rounds = kwargs.get("max_rounds", 20)
        self.max_total_compacts = kwargs.get("max_total_compacts", 10)
        self.consecutive_same_feedback_limit = kwargs.get("consecutive_same_feedback_limit", 3)
        self.same_feedback_similarity_threshold = kwargs.get("same_feedback_similarity_threshold", 0.9)
        self.judge_show_prompt = kwargs.get("judge_show_prompt", False)
        self.judge_model = None
        self.judge_provider = None
        self.judge_tools_enabled = False
        self.judge_allowed_tools = []
        self.judge_allowed_tool_groups = []
        self.persist_state = kwargs.get("persist_state", False)
        self.max_stuck_recoveries = kwargs.get("max_stuck_recoveries", 3)


class _FakeCfg:
    def __init__(self, project_root, **gm_kwargs):
        self.project_root = project_root
        self.goal_mode = _FakeGoalModeCfg(**gm_kwargs)


def test_goal_runner_done_on_first_round(monkeypatch, tmp_path):
    agent = FakeAgent(outputs=["did the thing"])
    cfg = _FakeCfg(tmp_path)
    spec = _confirmed_spec()

    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge",
        lambda **kw: "**结论**\n全部通过\nGOAL_STATUS: DONE",
    )

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    result = runner.run()

    assert result.status == "done"
    assert result.rounds_used == 0  # DONE 发生在第一轮判定时，尚未 round+=1
    assert agent._call_idx == 1


def test_goal_runner_continue_then_done(monkeypatch, tmp_path):
    agent = FakeAgent(outputs=["attempt 1", "attempt 2"])
    cfg = _FakeCfg(tmp_path)
    spec = _confirmed_spec()

    responses = iter([
        "**反馈**\n还差一点\nGOAL_STATUS: CONTINUE",
        "**结论**\n完成了\nGOAL_STATUS: DONE",
    ])
    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge",
        lambda **kw: next(responses),
    )

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    result = runner.run()

    assert result.status == "done"
    assert result.rounds_used == 1
    assert agent._call_idx == 2


def test_goal_runner_need_compact_triggers_compact_without_consuming_round(monkeypatch, tmp_path):
    agent = FakeAgent(outputs=["attempt 1", "attempt 2"])
    cfg = _FakeCfg(tmp_path)
    spec = _confirmed_spec()

    responses = iter([
        "GOAL_STATUS: NEED_COMPACT",
        "GOAL_STATUS: DONE",
    ])
    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge",
        lambda **kw: next(responses),
    )

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    result = runner.run()

    assert result.status == "done"
    assert agent.compact_calls == 1
    assert result.rounds_used == 0  # NEED_COMPACT 不消耗轮次预算


def test_goal_runner_hit_max_turns_compacts_and_retries(monkeypatch, tmp_path):
    agent = FakeAgent(
        outputs=["partial", "finished"],
        hit_max_turns_flags=[True, False],
    )
    cfg = _FakeCfg(tmp_path)
    spec = _confirmed_spec()

    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge",
        lambda **kw: "GOAL_STATUS: DONE",
    )

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    result = runner.run()

    assert result.status == "done"
    assert agent.compact_calls == 1
    # 第一步因 hit_max_turns 被拦截，没有调用 judge；第二步才调用 judge 并 DONE
    assert agent._call_idx == 2


def test_goal_runner_max_rounds_exhausted(monkeypatch, tmp_path):
    agent = FakeAgent(outputs=[f"attempt {i}" for i in range(3)])
    cfg = _FakeCfg(tmp_path, max_rounds=3, consecutive_same_feedback_limit=100)
    spec = _confirmed_spec()

    # 每次反馈都不同，避免触发"卡住"提前终止，纯粹测试 max_rounds 耗尽路径
    responses = iter([
        "GOAL_STATUS: CONTINUE\nfeedback A",
        "GOAL_STATUS: CONTINUE\nfeedback B varies xyz",
        "GOAL_STATUS: CONTINUE\nfeedback C totally different 123",
    ])
    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge",
        lambda **kw: next(responses),
    )

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    result = runner.run()

    assert result.status == "max_rounds_exhausted"
    assert result.rounds_used == 3


def test_goal_runner_stuck_on_repeated_feedback(monkeypatch, tmp_path):
    # Need enough outputs for: 3 rounds * 3 recoveries + 3 final rounds = 12+ rounds
    agent = FakeAgent(outputs=[f"attempt {i}" for i in range(20)])
    cfg = _FakeCfg(tmp_path, max_rounds=20, consecutive_same_feedback_limit=3)
    spec = _confirmed_spec()

    same_feedback = "GOAL_STATUS: CONTINUE\n还是同样的问题，反复卡在这里"
    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge",
        lambda **kw: same_feedback,
    )

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    result = runner.run()

    assert result.status == "stuck"
    # 应该在耗尽 max_rounds(20) 之前就因为反馈雷同提前终止
    assert result.rounds_used < 20


def test_goal_runner_state_survives_mid_round_crash(monkeypatch, tmp_path):
    """模拟"进程在第 2 轮执行中被强制杀死"：第 1 轮结束时落盘的 running 状态
    应该在进程重启（这里用 find_resumable_session 模拟）后仍然能被找到。"""
    agent = FakeAgent(outputs=["attempt 1", "attempt 2"])
    cfg = _FakeCfg(tmp_path, persist_state=True)
    spec = _confirmed_spec()

    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge",
        lambda **kw: "GOAL_STATUS: CONTINUE\nkeep going",
    )

    # 第 2 次 run_turn 模拟进程被 kill -9：直接抛异常，代表整个进程终止，
    # 不会走到任何 finally/except 清理逻辑。
    original_run_turn = agent.run_turn

    def crashing_run_turn(prompt):
        if agent._call_idx == 1:  # 第 2 次调用（0-indexed 已经调用过一次）
            raise RuntimeError("simulated kill -9 mid round 2")
        return original_run_turn(prompt)

    agent.run_turn = crashing_run_turn

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    with pytest.raises(RuntimeError):
        runner.run()

    # 崩溃发生在第 2 轮的 run_turn 里，第 1 轮结束时应该已经落盘 round=1, status=running
    sid = find_resumable_session(tmp_path)
    assert sid == agent.session_id, (
        "进程被杀死后重启，应该能通过 find_resumable_session 找到未完成的 goal"
    )

    paths = AgentPaths(project_root=tmp_path)
    store = GoalStateStore(paths, agent.session_id)
    state = store.load()
    assert state.status == "running"
    assert state.round == 1


def test_goal_runner_show_judge_prompt_switch(monkeypatch, tmp_path):
    import mini_agent.goal_mode.runner as runner_mod

    printed = []
    monkeypatch.setattr(runner_mod.R.console, "print", lambda *a, **kw: printed.append(" ".join(str(x) for x in a)))

    agent = FakeAgent(outputs=["attempt 1"])
    cfg = _FakeCfg(tmp_path, judge_show_prompt=True)
    spec = _confirmed_spec()

    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge",
        lambda **kw: "GOAL_STATUS: DONE",
    )

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    runner.run()

    joined = "\n".join(printed)
    assert "GoalJudge 输入 Prompt" in joined
    assert spec.goal_text in joined


def test_goal_runner_hides_judge_prompt_by_default(monkeypatch, tmp_path):
    import mini_agent.goal_mode.runner as runner_mod

    printed = []
    monkeypatch.setattr(runner_mod.R.console, "print", lambda *a, **kw: printed.append(" ".join(str(x) for x in a)))

    agent = FakeAgent(outputs=["attempt 1"])
    cfg = _FakeCfg(tmp_path)  # judge_show_prompt 默认 False
    spec = _confirmed_spec()

    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge",
        lambda **kw: "GOAL_STATUS: DONE",
    )

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    runner.run()

    joined = "\n".join(printed)
    assert "GoalJudge 输入 Prompt" not in joined


def test_goal_runner_pause_preserves_running_status(tmp_path):
    """Ctrl-C 中断的真实意图是"先停一下，之后还想继续"，pause() 必须保持
    status=running，不能被误存成 cancelled（否则 /goal resume 会找不到它）。"""
    agent = FakeAgent(outputs=["attempt 1"])
    cfg = _FakeCfg(tmp_path, persist_state=True)
    spec = _confirmed_spec()

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    runner._round = 3  # 模拟已经跑了几轮
    runner.pause()

    paths = AgentPaths(project_root=tmp_path)
    store = GoalStateStore(paths, agent.session_id)
    state = store.load()
    assert state.status == "running"
    assert state.round == 3

    sid = find_resumable_session(tmp_path)
    assert sid == agent.session_id


def test_goal_runner_cancel_sets_cancelled_status(tmp_path):
    """显式 /goal cancel（对应 cancel()）才应该真正标记为 cancelled，
    和 pause() 的语义严格区分开。"""
    agent = FakeAgent(outputs=["attempt 1"])
    cfg = _FakeCfg(tmp_path, persist_state=True)
    spec = _confirmed_spec()

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    runner._round = 2
    runner.cancel()

    paths = AgentPaths(project_root=tmp_path)
    store = GoalStateStore(paths, agent.session_id)
    state = store.load()
    assert state.status == "cancelled"

    # cancelled 状态不应该被 find_resumable_session 找到
    assert find_resumable_session(tmp_path) is None


def test_goal_runner_rejects_unconfirmed_spec(tmp_path):
    agent = FakeAgent(outputs=["x"])
    cfg = _FakeCfg(tmp_path)
    spec = GoalSpec(goal_text="x", acceptance_criteria=["y"], confirmed=False)

    with pytest.raises(ValueError):
        GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)


def test_goal_runner_persists_state_across_rounds(monkeypatch, tmp_path):
    agent = FakeAgent(outputs=["a1", "a2"])
    cfg = _FakeCfg(tmp_path, persist_state=True)
    spec = _confirmed_spec()

    responses = iter([
        "GOAL_STATUS: CONTINUE\nkeep going",
        "GOAL_STATUS: DONE",
    ])
    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge",
        lambda **kw: next(responses),
    )

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    result = runner.run()

    paths = AgentPaths(project_root=tmp_path)
    store = GoalStateStore(paths, agent.session_id)
    state = store.load()
    assert state.status == "done"
    assert state.round == 1
