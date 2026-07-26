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
    GoalSpecBuildError,
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
    transcript, truncated, has_compact = _extract_history_transcript(history)
    assert "帮我修一下这个 bug" in transcript
    assert "已定位到问题" in transcript
    assert "should be ignored" not in transcript
    assert truncated is False
    assert has_compact is False


def test_extract_history_transcript_extracts_text_blocks_from_list_content():
    history = [
        {"role": "user", "content": [{"type": "text", "text": "看看这个"}, {"type": "image", "url": "x"}]},
    ]
    transcript, truncated, has_compact = _extract_history_transcript(history)
    assert "看看这个" in transcript
    assert truncated is False
    assert has_compact is False


def test_extract_history_transcript_truncates_by_message_count():
    history = [{"role": "user", "content": f"msg-{i}"} for i in range(50)]
    transcript, truncated, has_compact = _extract_history_transcript(history, max_messages=10, max_chars=100000)
    assert truncated is True
    assert "msg-49" in transcript
    assert "msg-0" not in transcript
    assert has_compact is False


def test_extract_history_transcript_empty_history_returns_empty():
    transcript, truncated, has_compact = _extract_history_transcript([])
    assert transcript == ""
    assert truncated is False
    assert has_compact is False


def test_extract_history_transcript_skips_session_resume_placeholder():
    history = [
        {"role": "user", "content": "[Previous session summary]", "_type": "session_resume"},
        {"role": "assistant", "content": "some real summary text", "_type": "compact_summary"},
    ]
    transcript, truncated, has_compact = _extract_history_transcript(history)
    assert "[Previous session summary]" not in transcript
    assert "some real summary text" in transcript
    assert has_compact is True


def test_extract_history_transcript_labels_compact_summary_specially():
    history = [
        {
            "role": "assistant",
            "content": "## Goal\nfix the bug\n## Pending / Next Steps\nadd tests",
            "_type": "compact_summary",
        },
    ]
    transcript, truncated, has_compact = _extract_history_transcript(history)
    assert has_compact is True
    assert "历史摘要（/compact 生成）" in transcript
    assert "add tests" in transcript


def test_extract_history_transcript_widens_char_budget_for_compact_summary():
    long_summary = "x" * 5000
    history = [
        {"role": "assistant", "content": long_summary, "_type": "compact_summary"},
    ]
    # 普通对话的 max_chars=6000 场景下 5000 字符不会被截断；这里验证
    # has_compact_summary 时字符预算翻倍生效（不会被更严格的默认值提前截断）。
    transcript, truncated, has_compact = _extract_history_transcript(history, max_chars=3000)
    assert has_compact is True
    assert long_summary in transcript




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


def test_build_from_history_retries_on_unparseable_json_then_succeeds(monkeypatch):
    builder = GoalSpecBuilder.__new__(GoalSpecBuilder)
    calls = {"n": 0}

    def fake_run_builder(prompt):
        calls["n"] += 1
        if calls["n"] == 1:
            return "not json at all, model rambled"
        return '{"goal_text": "recovered goal", "acceptance_criteria": ["c1"]}'

    monkeypatch.setattr(builder, "_run_builder", fake_run_builder)
    history = [{"role": "user", "content": "do the thing"}]
    spec = builder.build_from_history(history)
    assert calls["n"] == 2
    assert spec.goal_text == "recovered goal"
    assert spec.acceptance_criteria == ["c1"]


def test_build_from_history_raises_after_two_unparseable_attempts(monkeypatch):
    builder = GoalSpecBuilder.__new__(GoalSpecBuilder)
    monkeypatch.setattr(builder, "_run_builder", lambda prompt: "still not json")
    history = [{"role": "user", "content": "do the thing"}]
    with pytest.raises(GoalSpecBuildError):
        builder.build_from_history(history)


def test_build_from_history_works_after_compact(monkeypatch):
    builder = GoalSpecBuilder.__new__(GoalSpecBuilder)
    monkeypatch.setattr(
        builder,
        "_run_builder",
        lambda prompt: '{"goal_text": "补齐 mini_agent 的单测覆盖率", '
        '"acceptance_criteria": ["pytest --cov 达到 80%"], '
        '"verification_method": "run_command", '
        '"verification_command": "pytest --cov=src"}',
    )
    # 模拟 /compact 之后的典型历史结构：
    # [session_resume 占位符, compact_summary 结构化摘要, skill_context]
    history = [
        {"role": "user", "content": "[Previous session summary]", "_type": "session_resume"},
        {
            "role": "assistant",
            "content": (
                "## Goal\n把测试覆盖率提上去\n"
                "## Current State\n核心模块已实现，尚无单测\n"
                "## Pending / Next Steps\n为 core.py 补充单测并跑通 pytest --cov"
            ),
            "_type": "compact_summary",
        },
        {"role": "user", "content": "skill: pytest usage notes", "_type": "skill_context"},
    ]
    spec = builder.build_from_history(history)
    assert spec.goal_text == "补齐 mini_agent 的单测覆盖率"
    assert spec.acceptance_criteria == ["pytest --cov 达到 80%"]




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
    # [Phase 5] 输出格式改为结构化 JSON（见 role_agents/verdict.py），
    # 具体的 JSON 输出指令由 fragments/judge_json_output.md 渲染注入，
    # 这里只确认模板里留有对应的占位符，具体渲染效果见 run_goal_judge 的调用。
    assert "{{json_output_instructions}}" in rendered
    assert not rendered.startswith("#")  # 确认注释头没有残留

    from mini_agent.role_agents.goal_judge import run_goal_judge  # noqa: F401 (import 校验 wiring 不报错)
    full_rendered = pm.render(
        "system/goal_judge",
        json_output_instructions=pm.fragment(
            "judge_json_output", "JSON_OUTPUT_INSTRUCTIONS",
            valid_statuses="DONE | CONTINUE | NEED_COMPACT",
            feedback_hint="...", example_status="DONE", example_feedback="...",
        ),
    )
    assert "\"status\"" in full_rendered
    assert "DONE | CONTINUE | NEED_COMPACT" in full_rendered


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


def test_goal_spec_builder_calls_llm_directly_not_via_agent(monkeypatch, tmp_path):
    """[REFACTOR 2] GoalSpecBuilder 不应再构造任何 Agent（无 MCP 连接/工具循环/
    轮次预算），而是直接通过 LLMHelper 发起一次单轮 chat completion。

    这个测试取代了旧版 test_goal_spec_builder_uses_distinct_agent_name——旧测试
    断言的是"内部 Agent 用了专属 agent_name"，这个概念随着 Agent 机制被整体
    移除已经不再适用。新测试改为断言：
      1. mini_agent.agent.Agent 完全没有被实例化（monkeypatch 成一个会立刻
         报错的假类，只要被调用测试就会失败）；
      2. GoalSpecBuilder 实际调用的是 LLMHelper.ask()，且传入了正确解析出的
         model/provider（覆盖生效，等价于旧版"清空 fallback chain、专属模型"
         的效果）。
    """
    import mini_agent.agent as agent_mod
    from mini_agent.config.loader import load_config
    from mini_agent.llm.service import LLMHelper

    def _agent_should_not_be_constructed(*args, **kwargs):
        raise AssertionError("GoalSpecBuilder 不应该构造 Agent 实例")

    monkeypatch.setattr(agent_mod, "Agent", _agent_should_not_be_constructed)

    captured = {}

    def fake_ask(self, prompt, *, system="", max_retries=3, retry_policy=None,
                 override_model=None, override_provider=None, override_temperature=None):
        captured["system"] = system
        captured["override_model"] = override_model
        captured["override_provider"] = override_provider
        return '{"goal_text": "g", "acceptance_criteria": ["c"]}'

    monkeypatch.setattr(LLMHelper, "ask", fake_ask)

    base_cfg = load_config(
        project_root=tmp_path, verbose=False, sandbox=True,
        auto_approve=True, model="claude-sonnet-4-6",
    )
    base_cfg.api_key = "sk-fake"

    builder = GoalSpecBuilder(base_cfg)
    spec = builder.build_initial("do the thing")

    assert spec.goal_text == "g"
    assert spec.acceptance_criteria == ["c"]
    assert captured["override_model"] == "claude-sonnet-4-6"
    assert "system" in captured and captured["system"]


def test_goal_spec_builder_reuses_injected_llm_helper(tmp_path):
    """显式注入 llm_helper 时应直接复用它（而不是各处自建一条新的 client_pool），
    这样从活跃 Agent 调用时天然跟随 /model、/provider 的实时切换。"""
    from mini_agent.config.loader import load_config

    class _FakeHelper:
        def __init__(self):
            self.calls = []

        def ask(self, prompt, *, system="", max_retries=3, retry_policy=None,
                override_model=None, override_provider=None, override_temperature=None):
            self.calls.append(prompt)
            return '{"goal_text": "g2", "acceptance_criteria": ["c2"]}'

    base_cfg = load_config(
        project_root=tmp_path, verbose=False, sandbox=True,
        auto_approve=True, model="claude-sonnet-4-6",
    )
    base_cfg.api_key = "sk-fake"

    fake_helper = _FakeHelper()
    builder = GoalSpecBuilder(base_cfg, llm_helper=fake_helper)
    spec = builder.build_initial("do another thing")

    assert spec.goal_text == "g2"
    assert len(fake_helper.calls) == 1


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

    def compact_with_skills(self, goal_hint: str = ""):
        self.compact_calls += 1
        self.last_goal_hint = goal_hint
        return f"[fake summary #{self.compact_calls}]"


def _confirmed_spec():
    return GoalSpec(goal_text="do the thing", acceptance_criteria=["it works"], confirmed=True)


class _FakeGoalModeCfg:
    def __init__(self, **kwargs):
        self.max_rounds = kwargs.get("max_rounds", 20)
        self.max_total_compacts = kwargs.get("max_total_compacts", 10)
        self.consecutive_same_feedback_limit = kwargs.get("consecutive_same_feedback_limit", 3)
        self.same_feedback_similarity_threshold = kwargs.get("same_feedback_similarity_threshold", 0.9)
        self.max_stuck_recoveries = kwargs.get("max_stuck_recoveries", 3)
        self.judge_show_prompt = kwargs.get("judge_show_prompt", False)
        self.judge_model = None
        self.judge_provider = None
        self.judge_tools_enabled = False
        self.judge_allowed_tools = []
        self.judge_allowed_tool_groups = []
        self.persist_state = kwargs.get("persist_state", False)
        self.progress_judge_mode = kwargs.get("progress_judge_mode", "llm")
        self.criteria_tracking_enabled = kwargs.get("criteria_tracking_enabled", True)
        self.stuck_recovery_attempted_paths_enabled = kwargs.get("stuck_recovery_attempted_paths_enabled", True)
        self.failure_lesson_enabled = kwargs.get("failure_lesson_enabled", True)
        self.dead_ends_persist_enabled = kwargs.get("dead_ends_persist_enabled", True)
        self.auto_verify_enabled = kwargs.get("auto_verify_enabled", True)
        self.auto_verify_timeout = kwargs.get("auto_verify_timeout", 120)
        self.auto_verify_output_tail_lines = kwargs.get("auto_verify_output_tail_lines", 40)
        self.progress_score_enabled = kwargs.get("progress_score_enabled", True)
        self.process_integrity_check_enabled = kwargs.get("process_integrity_check_enabled", True)
        self.pseudo_progress_detection_enabled = kwargs.get("pseudo_progress_detection_enabled", True)
        self.pseudo_progress_window = kwargs.get("pseudo_progress_window", 5)
        self.pseudo_progress_stagnation_threshold = kwargs.get("pseudo_progress_stagnation_threshold", 0.15)
        self.pseudo_progress_max_score_cap = kwargs.get("pseudo_progress_max_score_cap", 0.5)
        self.proactive_compact_enabled = kwargs.get("proactive_compact_enabled", False)
        self.exploring_compact_interval = kwargs.get("exploring_compact_interval", 2)
        self.phase_convergence_window = kwargs.get("phase_convergence_window", 3)
        self.replan_proposal_mode = kwargs.get("replan_proposal_mode", "off")
        self.spec_builder_model = None
        self.spec_builder_provider = None


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
    # 需要足够的输出覆盖：3轮触发卡住 -> compact -> 3轮触发卡住 -> compact -> 3轮触发卡住 -> compact -> 终止
    # 共 3*3 + 3 = 12 轮，所以需要至少 12 个输出
    agent = FakeAgent(outputs=[f"attempt {i}" for i in range(15)])
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


# ── [next_doc/goal_mode_completion_improvement_plan.md] 改造项一～五 ────────

import json


def test_stuck_detector_observe_signal_recovers_then_gives_up():
    from mini_agent.role_agents.stuck_detector import StuckDetector, StuckSignal

    d = StuckDetector(similarity_threshold=0.9, consecutive_limit=3, max_recoveries=1)
    # 连续两次 is_same=True 才会达到 (consecutive_limit - 1) = 2 次触发
    assert d.observe_signal(is_same=True) is StuckSignal.NONE
    assert d.observe_signal(is_same=True) is StuckSignal.RECOVER  # 用掉唯一一次恢复额度
    assert d.observe_signal(is_same=True) is StuckSignal.NONE
    assert d.observe_signal(is_same=True) is StuckSignal.GIVE_UP  # 恢复额度已耗尽


def test_stuck_detector_observe_signal_resets_on_progress():
    from mini_agent.role_agents.stuck_detector import StuckDetector, StuckSignal

    d = StuckDetector(similarity_threshold=0.9, consecutive_limit=3, max_recoveries=2)
    assert d.observe_signal(is_same=True) is StuckSignal.NONE
    assert d.observe_signal(is_same=False) is StuckSignal.NONE  # 有进展，重置计数
    assert d.consecutive_same == 0
    assert d.recoveries_used == 0


def test_goal_state_roundtrip_includes_criteria_and_progress_history(tmp_path):
    paths = AgentPaths(project_root=tmp_path)
    store = GoalStateStore(paths, "sess-checklist")
    state = GoalState(
        status="running",
        session_id="sess-checklist",
        round=2,
        criteria_status=[{"index": 1, "text": "it works", "passed": True, "evidence": "ok", "last_updated_round": 2}],
        recent_progress_reasons=[{"round": 2, "progress": "SUBSTANTIVE_ADVANCE", "reason": "测试通过"}],
    )
    store.save(state)
    loaded = store.load()
    assert loaded.criteria_status[0]["passed"] is True
    assert loaded.recent_progress_reasons[0]["progress"] == "SUBSTANTIVE_ADVANCE"


def _judge_json(status, feedback="", progress=None, progress_reason="", checklist=None):
    d = {"status": status, "feedback": feedback}
    if progress is not None:
        d["progress"] = progress
        d["progress_reason"] = progress_reason
    if checklist is not None:
        d["checklist"] = checklist
    return json.dumps(d, ensure_ascii=False)


def test_goal_runner_llm_progress_catches_stuck_despite_varying_feedback_text(monkeypatch, tmp_path):
    """[改造项一] 每轮反馈文本都不同（若用纯文本相似度规则不会被判定为卡住），
    但 GoalJudge 判断 progress=SAME_APPROACH_NO_GAIN——应该仍然被识别为卡住，
    这正是规则算法的假阴性场景。"""
    agent = FakeAgent(outputs=[f"attempt {i}" for i in range(15)])
    cfg = _FakeCfg(tmp_path, max_rounds=20, consecutive_same_feedback_limit=3, max_stuck_recoveries=0)
    spec = _confirmed_spec()

    counter = {"i": 0}

    def fake_judge(**kw):
        counter["i"] += 1
        # 每轮反馈文本都刻意造得完全不同，纯文本相似度规则不会命中
        return _judge_json(
            "CONTINUE",
            feedback=f"完全不同的措辞 #{counter['i']} xyz123",
            progress="SAME_APPROACH_NO_GAIN",
            progress_reason=f"第 {counter['i']} 轮仍然卡在同一个错误上",
        )

    monkeypatch.setattr("mini_agent.role_agents.goal_judge.run_goal_judge", lambda **kw: fake_judge(**kw))

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    result = runner.run()

    assert result.status == "stuck"
    assert result.rounds_used < 20


def test_goal_runner_llm_progress_avoids_false_positive_on_similar_feedback(monkeypatch, tmp_path):
    """[改造项一] 反馈文本结构高度相似（若用纯文本相似度规则会被误判卡住），
    但 GoalJudge 判断每轮都是 SUBSTANTIVE_ADVANCE——不应该被提前终止为 stuck，
    应该正常跑到 max_rounds 耗尽（证明修复了假阳性场景）。"""
    agent = FakeAgent(outputs=[f"attempt {i}" for i in range(5)])
    cfg = _FakeCfg(tmp_path, max_rounds=5, consecutive_same_feedback_limit=3)
    spec = _confirmed_spec()

    def fake_judge(**kw):
        # 反馈文本结构高度相似（纯文本相似度规则会误判卡住）
        return _judge_json(
            "CONTINUE",
            feedback="测试 A 通过，测试 B 仍失败",
            progress="SUBSTANTIVE_ADVANCE",
            progress_reason="又修复了一个新的失败点",
        )

    monkeypatch.setattr("mini_agent.role_agents.goal_judge.run_goal_judge", lambda **kw: fake_judge(**kw))

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    result = runner.run()

    assert result.status == "max_rounds_exhausted"
    assert result.rounds_used == 5


def test_goal_runner_falls_back_to_text_similarity_when_progress_missing(monkeypatch, tmp_path):
    """[改造项一兜底] GoalJudge 输出不是合法 JSON（未按扩展 schema 输出）时，
    progress 字段拿不到，应自动回退到原有的文本相似度规则，行为与升级前一致。"""
    agent = FakeAgent(outputs=[f"attempt {i}" for i in range(15)])
    cfg = _FakeCfg(tmp_path, max_rounds=20, consecutive_same_feedback_limit=3)
    spec = _confirmed_spec()

    same_feedback = "GOAL_STATUS: CONTINUE\n还是同样的问题，反复卡在这里"
    monkeypatch.setattr("mini_agent.role_agents.goal_judge.run_goal_judge", lambda **kw: same_feedback)

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    result = runner.run()

    assert result.status == "stuck"


def test_goal_runner_tracks_criteria_checklist(monkeypatch, tmp_path):
    """[改造项三] GoalJudge 输出 checklist 后，GoalRunner 应该更新
    self._criteria_status 对应条目的 passed/evidence。"""
    agent = FakeAgent(outputs=["a1", "a2"])
    cfg = _FakeCfg(tmp_path)
    spec = GoalSpec(goal_text="do X", acceptance_criteria=["标准一", "标准二"], confirmed=True)

    responses = iter([
        _judge_json(
            "CONTINUE", feedback="标准一过了",
            progress="SUBSTANTIVE_ADVANCE", progress_reason="标准一通过了",
            checklist=[{"index": 1, "passed": True, "evidence": "已验证"},
                       {"index": 2, "passed": False, "evidence": "尚未实现"}],
        ),
        _judge_json("DONE", feedback="全部完成"),
    ])
    monkeypatch.setattr("mini_agent.role_agents.goal_judge.run_goal_judge", lambda **kw: next(responses))

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    runner.run()

    status_by_index = {c["index"]: c for c in runner._criteria_status}
    assert status_by_index[1]["passed"] is True
    assert status_by_index[1]["evidence"] == "已验证"
    assert status_by_index[2]["passed"] is False


def test_goal_runner_stuck_recovery_hint_includes_attempted_paths(monkeypatch, tmp_path):
    """[改造项二] 卡住恢复时注入的提示应该包含此前几轮的 progress_reason，
    而不只是通用话术。"""
    agent = FakeAgent(outputs=[f"attempt {i}" for i in range(10)])
    cfg = _FakeCfg(tmp_path, max_rounds=20, consecutive_same_feedback_limit=3, max_stuck_recoveries=3)
    spec = _confirmed_spec()

    counter = {"i": 0}

    def fake_judge(**kw):
        counter["i"] += 1
        return _judge_json(
            "CONTINUE", feedback=f"没有进展 #{counter['i']}",
            progress="SAME_APPROACH_NO_GAIN",
            progress_reason=f"曾尝试方案 {counter['i']}，验证无效",
        )

    monkeypatch.setattr("mini_agent.role_agents.goal_judge.run_goal_judge", lambda **kw: fake_judge(**kw))

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    runner.run()

    injected_hints = [
        e.get("content", "") for e in agent._hist.entries
        if isinstance(e, dict) and "曾尝试方案" in e.get("content", "")
    ]
    assert injected_hints, "卡住恢复提示应包含此前的 progress_reason（已验证无效的方向）"


def test_goal_runner_writes_failure_lesson_on_stuck(monkeypatch, tmp_path):
    """[改造项五] goal 因 stuck 终止时，应该把失败经验写入 memory backend。"""

    class _FakeMemory:
        def __init__(self):
            self.entries = []

        def add(self, entry):
            self.entries.append(entry)

    agent = FakeAgent(outputs=[f"attempt {i}" for i in range(15)])
    agent._memory = _FakeMemory()
    cfg = _FakeCfg(tmp_path, max_rounds=20, consecutive_same_feedback_limit=3, max_stuck_recoveries=0)
    spec = _confirmed_spec()

    def fake_judge(**kw):
        return _judge_json(
            "CONTINUE", feedback="卡住了",
            progress="SAME_APPROACH_NO_GAIN", progress_reason="反复尝试同一种修复没有效果",
        )

    monkeypatch.setattr("mini_agent.role_agents.goal_judge.run_goal_judge", lambda **kw: fake_judge(**kw))

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    result = runner.run()

    assert result.status == "stuck"
    assert len(agent._memory.entries) == 1
    lesson = agent._memory.entries[0]
    assert lesson.entry_type == "lesson"
    assert lesson.source == "goal_mode_failure"


def test_goal_runner_no_lesson_written_when_disabled(monkeypatch, tmp_path):
    """failure_lesson_enabled=False 时不应该写入任何 lesson。"""

    class _FakeMemory:
        def __init__(self):
            self.entries = []

        def add(self, entry):
            self.entries.append(entry)

    agent = FakeAgent(outputs=[f"attempt {i}" for i in range(15)])
    agent._memory = _FakeMemory()
    cfg = _FakeCfg(
        tmp_path, max_rounds=20, consecutive_same_feedback_limit=3, max_stuck_recoveries=0,
        failure_lesson_enabled=False,
    )
    spec = _confirmed_spec()

    def fake_judge(**kw):
        return _judge_json(
            "CONTINUE", feedback="卡住了",
            progress="SAME_APPROACH_NO_GAIN", progress_reason="反复尝试同一种修复没有效果",
        )

    monkeypatch.setattr("mini_agent.role_agents.goal_judge.run_goal_judge", lambda **kw: fake_judge(**kw))

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    result = runner.run()

    assert result.status == "stuck"
    assert agent._memory.entries == []


# ── [goal_mode_stuck_compact_plan.md §1.2] Dead-end 持久清单 ────────────────

def test_goal_state_dead_ends_roundtrip(tmp_path):
    """GoalState 应该能正确落盘/恢复 dead_ends 字段。"""
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.goal_mode.state import GoalStateStore

    paths = AgentPaths(project_root=tmp_path)
    store = GoalStateStore(paths, "sess-dead-ends")
    state = GoalState(dead_ends=[{"round": 2, "progress": "SAME_APPROACH_NO_GAIN", "reason": "同一个错误反复出现"}])
    store.save(state)
    loaded = store.load()
    assert loaded.dead_ends[0]["reason"] == "同一个错误反复出现"


def test_goal_runner_dead_ends_survive_progress_reasons_window_eviction(monkeypatch, tmp_path):
    """[§1.2] recent_progress_reasons 是滚动窗口，会被后续记录冲掉；
    但 dead_ends 是持久清单，不应该被冲掉——多轮之后第一次记录的 dead-end
    依然应该出现在 stuck 恢复注入的提示里。"""
    agent = FakeAgent(outputs=[f"attempt {i}" for i in range(15)])
    # 窗口上限固定为 max(3, consecutive_same_feedback_limit) = 3，第一条会被冲掉
    cfg = _FakeCfg(tmp_path, max_rounds=20, consecutive_same_feedback_limit=3, max_stuck_recoveries=1)
    spec = _confirmed_spec()

    counter = {"i": 0}

    def fake_judge(**kw):
        counter["i"] += 1
        # 第一轮给一个独特的、可识别的失败理由
        if counter["i"] == 1:
            reason = "尝试了直接修改 UNIQUE_MARKER_PATH_A，但发现前提假设不成立"
        else:
            reason = f"反复尝试同一种修复没有效果（第{counter['i']}次）"
        return _judge_json(
            "CONTINUE", feedback="卡住了",
            progress="SAME_APPROACH_NO_GAIN", progress_reason=reason,
        )

    monkeypatch.setattr("mini_agent.role_agents.goal_judge.run_goal_judge", lambda **kw: fake_judge(**kw))

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    runner.run()

    # dead_ends 持久清单应该仍保留第一轮记录的具体理由
    assert any("UNIQUE_MARKER_PATH_A" in d["reason"] for d in runner._dead_ends)

    # 而窗口内的 _recent_progress_reasons 容量为 3，早期记录已被冲掉
    assert not any("UNIQUE_MARKER_PATH_A" in r["reason"] for r in runner._recent_progress_reasons)

    # 注入给主 agent 历史里的提示（compact 恢复时）应该包含这条持久化的 dead-end
    injected_texts = [e.get("content", "") for e in agent._hist.entries if isinstance(e, dict)]
    assert any("UNIQUE_MARKER_PATH_A" in t for t in injected_texts)


def test_goal_runner_dead_ends_dedup_near_duplicate(monkeypatch, tmp_path):
    """同一条（近似重复的）失败理由不应该被反复记录进 dead_ends。"""
    agent = FakeAgent(outputs=[f"attempt {i}" for i in range(15)])
    cfg = _FakeCfg(tmp_path, max_rounds=20, consecutive_same_feedback_limit=3, max_stuck_recoveries=0)
    spec = _confirmed_spec()

    same_reason = "反复尝试同一种修复方式没有任何效果"

    def fake_judge(**kw):
        return _judge_json(
            "CONTINUE", feedback="卡住了",
            progress="SAME_APPROACH_NO_GAIN", progress_reason=same_reason,
        )

    monkeypatch.setattr("mini_agent.role_agents.goal_judge.run_goal_judge", lambda **kw: fake_judge(**kw))

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    runner.run()

    assert len(runner._dead_ends) == 1


# ── [goal_mode_stuck_compact_plan.md §2.2] 自验证优先 ───────────────────────

def test_goal_runner_auto_verify_executes_command_and_passes_result_to_judge(monkeypatch, tmp_path):
    """GoalSpec.verification_command 非空时，GoalRunner 应该在调用判官之前
    程序化执行一次该命令，并把结果透传给 run_goal_judge 的 verification_result 参数。"""
    agent = FakeAgent(outputs=["did the thing"])
    cfg = _FakeCfg(tmp_path)
    spec = GoalSpec(
        goal_text="do the thing",
        acceptance_criteria=["it works"],
        confirmed=True,
        verification_method="run_command",
        verification_command="echo hello-verification",
    )

    captured = {}

    def fake_run_goal_judge(**kw):
        captured["verification_result"] = kw.get("verification_result")
        return "GOAL_STATUS: DONE\n全部通过"

    monkeypatch.setattr("mini_agent.role_agents.goal_judge.run_goal_judge", fake_run_goal_judge)

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    result = runner.run()

    assert result.status == "done"
    vr = captured["verification_result"]
    assert vr is not None
    assert vr["command"] == "echo hello-verification"
    assert vr["returncode"] == 0
    assert "hello-verification" in vr["stdout_tail"]


def test_goal_runner_auto_verify_disabled_by_config(monkeypatch, tmp_path):
    """auto_verify_enabled=False 时不应该执行验证命令（verification_result 应为 None）。"""
    agent = FakeAgent(outputs=["did the thing"])
    cfg = _FakeCfg(tmp_path, auto_verify_enabled=False)
    spec = GoalSpec(
        goal_text="do the thing",
        acceptance_criteria=["it works"],
        confirmed=True,
        verification_method="run_command",
        verification_command="echo should-not-run",
    )

    captured = {}

    def fake_run_goal_judge(**kw):
        captured["verification_result"] = kw.get("verification_result")
        return "GOAL_STATUS: DONE\n全部通过"

    monkeypatch.setattr("mini_agent.role_agents.goal_judge.run_goal_judge", fake_run_goal_judge)

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    runner.run()

    assert captured["verification_result"] is None


def test_goal_runner_auto_verify_no_command_returns_none(monkeypatch, tmp_path):
    """GoalSpec 未设置 verification_command 时，_run_verification_command 应返回 None。"""
    agent = FakeAgent(outputs=["did the thing"])
    cfg = _FakeCfg(tmp_path)
    spec = _confirmed_spec()

    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge",
        lambda **kw: "GOAL_STATUS: DONE\n全部通过",
    )

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    assert runner._run_verification_command() is None


def test_build_prompt_includes_self_verify_hint_when_command_set():
    """_build_prompt 应该在设置了 verification_command 时提醒主 Agent 自行执行验证。"""
    agent = FakeAgent(outputs=["x"])
    cfg = _FakeCfg(Path("/tmp"))
    spec = GoalSpec(
        goal_text="do the thing",
        acceptance_criteria=["it works"],
        confirmed=True,
        verification_method="run_command",
        verification_command="pytest -q",
    )
    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    prompt = runner._build_prompt()
    assert "pytest -q" in prompt
    assert "自验证要求" in prompt


# ── [goal_mode_stuck_compact_plan.md §3.1] 进展分数 ─────────────────────────

def test_progress_score_objective_delta_overrides_no_gain_subjective(monkeypatch, tmp_path):
    """客观 checklist 新增通过条目时，即使主观判断是 SAME_APPROACH_NO_GAIN，
    进展分数也不应该是 0——客观硬指标增量应该被体现出来。"""
    agent = FakeAgent(outputs=["attempt 1"])
    cfg = _FakeCfg(tmp_path)
    spec = GoalSpec(goal_text="do the thing", acceptance_criteria=["a", "b"], confirmed=True)

    def fake_judge(**kw):
        return _judge_json(
            "CONTINUE", feedback="still going",
            progress="SAME_APPROACH_NO_GAIN", progress_reason="没有新进展",
            checklist=[{"index": 1, "passed": True, "evidence": "通过了"},
                       {"index": 2, "passed": False, "evidence": "还没通过"}],
        )

    monkeypatch.setattr("mini_agent.role_agents.goal_judge.run_goal_judge", lambda **kw: fake_judge(**kw))

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    status, feedback, progress_info = runner._run_judge("attempt 1")

    assert progress_info["progress_score"] > 0  # delta=1 -> 0.3，覆盖了主观的 0.0


def test_progress_score_regression_caps_negative(monkeypatch, tmp_path):
    """checklist 通过条目退化（delta<0）时，进展分数应该明确为负，
    即使主观判断没有察觉到退步。"""
    agent = FakeAgent(outputs=["attempt 1", "attempt 2"])
    cfg = _FakeCfg(tmp_path)
    spec = GoalSpec(goal_text="do the thing", acceptance_criteria=["a", "b"], confirmed=True)

    calls = {"i": 0}

    def fake_judge(**kw):
        calls["i"] += 1
        if calls["i"] == 1:
            return _judge_json(
                "CONTINUE", feedback="progressing",
                progress="SUBSTANTIVE_ADVANCE", progress_reason="标准1通过了",
                checklist=[{"index": 1, "passed": True, "evidence": "通过"},
                           {"index": 2, "passed": False, "evidence": "未通过"}],
            )
        return _judge_json(
            "CONTINUE", feedback="regressed but judge doesn't say so",
            progress="SUBSTANTIVE_ADVANCE", progress_reason="看起来还行",
            checklist=[{"index": 1, "passed": False, "evidence": "退化了"},
                       {"index": 2, "passed": False, "evidence": "未通过"}],
        )

    monkeypatch.setattr("mini_agent.role_agents.goal_judge.run_goal_judge", lambda **kw: fake_judge(**kw))

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    runner._run_judge("attempt 1")
    _, _, progress_info2 = runner._run_judge("attempt 2")

    assert progress_info2["progress_score"] <= -0.5


def test_progress_score_disabled_by_config(monkeypatch, tmp_path):
    """progress_score_enabled=False 时不应该计算 progress_score。"""
    agent = FakeAgent(outputs=["attempt 1"])
    cfg = _FakeCfg(tmp_path, progress_score_enabled=False)
    spec = _confirmed_spec()

    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge",
        lambda **kw: _judge_json("CONTINUE", feedback="x", progress="SUBSTANTIVE_ADVANCE", progress_reason="y"),
    )

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    _, _, progress_info = runner._run_judge("attempt 1")

    assert "progress_score" not in progress_info


def test_goal_state_progress_score_fields_roundtrip(tmp_path):
    from mini_agent.storage.paths import AgentPaths
    from mini_agent.goal_mode.state import GoalStateStore

    paths = AgentPaths(project_root=tmp_path)
    store = GoalStateStore(paths, "sess-progress-score")
    state = GoalState(last_passed_count=2, progress_scores=[0.3, 1.0, -0.5])
    store.save(state)
    loaded = store.load()
    assert loaded.last_passed_count == 2
    assert loaded.progress_scores == [0.3, 1.0, -0.5]


# ── [goal_mode_stuck_compact_plan.md §1.1] 分级 compact ─────────────────────

def test_stuck_recovery_light_then_deep_compact(monkeypatch, tmp_path):
    """light_compact_max_recoveries=1 时，第一次卡住恢复应该不调用
    agent.compact_with_skills()（轻量：只注入提示），第二次恢复才应该
    真正触发 compact（深度）。"""
    agent = FakeAgent(outputs=[f"attempt {i}" for i in range(20)])
    cfg = _FakeCfg(
        tmp_path, max_rounds=30, consecutive_same_feedback_limit=3,
        max_stuck_recoveries=2,
    )
    cfg.goal_mode.light_compact_max_recoveries = 1
    spec = _confirmed_spec()

    same_feedback = "GOAL_STATUS: CONTINUE\n还是同样的问题，反复卡在这里"
    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge",
        lambda **kw: same_feedback,
    )

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    runner.run()

    # 第一次恢复是轻量（不 compact），第二次恢复才是深度（真正 compact）。
    # compact_calls 应该明显少于"每次恢复都 compact"时的次数。
    assert agent.compact_calls >= 1
    assert agent.compact_calls < runner._stuck_detector.recoveries_used + 2


def test_stuck_recovery_light_max_zero_falls_back_to_always_compact(monkeypatch, tmp_path):
    """light_compact_max_recoveries=0 时应该退化为升级前的"每次恢复都
    compact"行为。"""
    agent = FakeAgent(outputs=[f"attempt {i}" for i in range(15)])
    cfg = _FakeCfg(
        tmp_path, max_rounds=20, consecutive_same_feedback_limit=3,
        max_stuck_recoveries=1,
    )
    cfg.goal_mode.light_compact_max_recoveries = 0
    spec = _confirmed_spec()

    same_feedback = "GOAL_STATUS: CONTINUE\n还是同样的问题，反复卡在这里"
    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge",
        lambda **kw: same_feedback,
    )

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    runner.run()

    assert agent.compact_calls >= 1


# ── §2.1 process_flags 过程判断 / 结果判断分离 ──────────────────────────────

def test_goal_runner_process_flags_downgrade_done_to_continue(monkeypatch, tmp_path):
    """[goal_mode_stuck_compact_plan.md §2.1] 判官给出 DONE，但 process_flags
    非空（发现测试被弱化等投机行为）——GoalRunner 应该强制降级为 CONTINUE，
    不能直接放行 DONE，且反馈里要体现出过程问题。"""
    agent = FakeAgent(outputs=["did the thing", "did it properly"])
    cfg = _FakeCfg(tmp_path)
    spec = _confirmed_spec()

    responses = iter([
        _judge_json(
            "DONE", feedback="标准都通过了",
            checklist=[{"index": 1, "passed": True, "evidence": "测试通过"}],
        ),
    ])

    def fake_judge(**kw):
        d = json.loads(next(responses))
        d["process_flags"] = [
            {"concern": "test_weakened", "detail": "断言被改成了 assert True"}
        ]
        return json.dumps(d, ensure_ascii=False)

    monkeypatch.setattr("mini_agent.role_agents.goal_judge.run_goal_judge", lambda **kw: fake_judge(**kw))

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    status, feedback, progress_info = runner._run_judge("did the thing")

    assert status == "CONTINUE"
    assert "过程正当性问题" in feedback
    assert progress_info["process_flags"]
    assert progress_info["process_flags"][0]["concern"] == "test_weakened"


def test_goal_runner_process_flags_empty_allows_done(monkeypatch, tmp_path):
    """process_flags 为空数组（或缺省）时，DONE 判定不受影响，正常放行。"""
    agent = FakeAgent(outputs=["did the thing"])
    cfg = _FakeCfg(tmp_path)
    spec = _confirmed_spec()

    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge",
        lambda **kw: _judge_json(
            "DONE", feedback="都通过了",
            checklist=[{"index": 1, "passed": True, "evidence": "测试通过"}],
        ),
    )

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    status, feedback, progress_info = runner._run_judge("did the thing")

    assert status == "DONE"
    assert progress_info["process_flags"] == []


def test_goal_runner_process_integrity_disabled_allows_done_despite_flags(monkeypatch, tmp_path):
    """cfg.goal_mode.process_integrity_check_enabled=False 时，即使判官（异常地）
    给出了 process_flags，也完全不解析/不做降级检查，行为与升级前一致。"""
    agent = FakeAgent(outputs=["did the thing"])
    cfg = _FakeCfg(tmp_path, process_integrity_check_enabled=False)
    spec = _confirmed_spec()

    def fake_judge(**kw):
        d = json.loads(_judge_json(
            "DONE", feedback="都通过了",
            checklist=[{"index": 1, "passed": True, "evidence": "测试通过"}],
        ))
        d["process_flags"] = [{"concern": "test_weakened", "detail": "xxx"}]
        return json.dumps(d, ensure_ascii=False)

    monkeypatch.setattr("mini_agent.role_agents.goal_judge.run_goal_judge", lambda **kw: fake_judge(**kw))

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    status, feedback, progress_info = runner._run_judge("did the thing")

    assert status == "DONE"
    assert progress_info["process_flags"] == []


def test_goal_runner_process_flags_continue_status_unaffected(monkeypatch, tmp_path):
    """process_flags 非空但判官本来就给的是 CONTINUE（不是 DONE），不需要额外
    降级动作，只是把 process_flags 透传出来供上层可见。"""
    agent = FakeAgent(outputs=["still working"])
    cfg = _FakeCfg(tmp_path)
    spec = _confirmed_spec()

    def fake_judge(**kw):
        d = json.loads(_judge_json("CONTINUE", feedback="还没完成"))
        d["process_flags"] = [{"concern": "check_bypassed", "detail": "跳过了 lint 检查"}]
        return json.dumps(d, ensure_ascii=False)

    monkeypatch.setattr("mini_agent.role_agents.goal_judge.run_goal_judge", lambda **kw: fake_judge(**kw))

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    status, feedback, progress_info = runner._run_judge("still working")

    assert status == "CONTINUE"
    assert progress_info["process_flags"][0]["concern"] == "check_bypassed"


def test_run_goal_judge_includes_process_integrity_instructions_in_system_prompt(monkeypatch, tmp_path):
    """[goal_mode_stuck_compact_plan.md §2.1] process_integrity_enabled=True 时，
    system prompt（judge_cfg.system_extra）里应该拼接 PROCESS_INTEGRITY_INSTRUCTIONS
    片段（含 process_flags 字段说明）；False 时不应该出现。"""
    import mini_agent.agent as agent_mod
    from mini_agent.config.loader import load_config
    from mini_agent.orchestrator.agent_profiles import AgentProfile
    from mini_agent.role_agents.goal_judge import run_goal_judge

    captured = {}

    class FakeInnerAgent:
        def __init__(self, cfg, guard, registry, **kwargs):
            captured["system_extra"] = cfg.system_extra

        def run_turn(self, prompt):
            return json.dumps({"status": "CONTINUE", "feedback": "ok"})

    monkeypatch.setattr(agent_mod, "Agent", FakeInnerAgent)

    base_cfg = load_config(
        project_root=tmp_path, verbose=False, sandbox=True,
        auto_approve=True, model="claude-sonnet-4-6",
    )
    base_cfg.api_key = "sk-fake"

    profile = AgentProfile(name="goal_judge", role_type="goal_judge")
    spec = GoalSpec(goal_text="do X", acceptance_criteria=["a"], confirmed=True)

    marker = "test_weakened"  # PROCESS_INTEGRITY_INSTRUCTIONS 片段里的示例类别标签，
    # 只会在片段被拼接时出现，比泛泛搜索 "process_flags"（system prompt 核查
    # 原则第7条本身也会提到这个词）更能确认片段是否真的被拼接。

    run_goal_judge(profile, base_cfg, spec, "output", 1, "", process_integrity_enabled=True)
    assert marker in captured["system_extra"]

    run_goal_judge(profile, base_cfg, spec, "output", 1, "", process_integrity_enabled=False)
    assert marker not in captured["system_extra"]


# ── §3.2 伪进展趋势识别（ProgressTracker 接入 _check_stuck）──────────────────

def test_goal_runner_pseudo_progress_triggers_recovery_despite_nonzero_scores(monkeypatch, tmp_path):
    """[goal_mode_stuck_compact_plan.md §3.2] 每轮 checklist 都有微小的、
    非零的新增通过数（比如始终是 SUBSTANTIVE_ADVANCE），StuckDetector 本身
    识别不出这是"卡住"（因为每轮判断都是有进展），但进展分数长期平缓——
    ProgressTracker 应该识别出伪进展并触发和 stuck 同等级别的恢复流程。"""
    agent = FakeAgent(outputs=[f"attempt {i}" for i in range(15)])
    cfg = _FakeCfg(
        tmp_path, max_rounds=20, consecutive_same_feedback_limit=100,  # 让规则化 stuck 判定不生效
        max_stuck_recoveries=1,
        pseudo_progress_window=5, pseudo_progress_stagnation_threshold=0.15,
        pseudo_progress_max_score_cap=0.5,
    )
    spec = GoalSpec(goal_text="do X", acceptance_criteria=[f"c{i}" for i in range(1, 30)], confirmed=True)

    counter = {"i": 0}

    def fake_judge(**kw):
        counter["i"] += 1
        # 每轮都有恰好一个新标准从"未通过"变为"通过"（delta=1），但主观判断给
        # SAME_APPROACH_NO_GAIN（不是 SUBSTANTIVE_ADVANCE），使得
        # progress_score = max(0.0, 0.3*1) = 0.3——一个"平缓但非零"的分数，
        # 而不是被主观判断的高分（1.0）掩盖。consecutive_same_feedback_limit
        # 设得很高（100），确保规则化 StuckDetector 不会自己先触发，
        # 这样能确认真正起作用的是 ProgressTracker。
        idx = counter["i"]
        checklist = [
            {"index": i, "passed": (i <= idx), "evidence": "微量进展"} for i in range(1, idx + 2)
        ]
        return _judge_json(
            "CONTINUE", feedback=f"微小进展 #{idx}",
            progress="SAME_APPROACH_NO_GAIN", progress_reason="每轮都只多过一条标准，没有实质推进",
            checklist=checklist,
        )

    monkeypatch.setattr("mini_agent.role_agents.goal_judge.run_goal_judge", lambda **kw: fake_judge(**kw))

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    result = runner.run()

    assert result.status == "stuck"
    assert "伪进展" in result.final_report


def test_goal_runner_pseudo_progress_disabled_by_config(monkeypatch, tmp_path):
    """pseudo_progress_detection_enabled=False 时，即使进展分数长期平缓，
    也不应该触发额外的恢复流程——应该正常跑到 max_rounds 耗尽。"""
    agent = FakeAgent(outputs=[f"attempt {i}" for i in range(6)])
    cfg = _FakeCfg(
        tmp_path, max_rounds=6, consecutive_same_feedback_limit=100,
        max_stuck_recoveries=1, pseudo_progress_detection_enabled=False,
    )
    spec = GoalSpec(goal_text="do X", acceptance_criteria=[f"c{i}" for i in range(1, 30)], confirmed=True)

    counter = {"i": 0}

    def fake_judge(**kw):
        counter["i"] += 1
        idx = counter["i"]
        checklist = [
            {"index": i, "passed": (i <= idx), "evidence": "微量进展"} for i in range(1, idx + 2)
        ]
        return _judge_json(
            "CONTINUE", feedback=f"微小进展 #{idx}",
            progress="SUBSTANTIVE_ADVANCE", progress_reason="又推进了一小步",
            checklist=checklist,
        )

    monkeypatch.setattr("mini_agent.role_agents.goal_judge.run_goal_judge", lambda **kw: fake_judge(**kw))

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    result = runner.run()

    assert result.status == "max_rounds_exhausted"
    assert result.rounds_used == 6


def test_goal_runner_pseudo_progress_shares_recovery_budget_with_stuck_detector(monkeypatch, tmp_path):
    """伪进展触发的恢复应该消耗和 StuckDetector 规则判定同一份
    max_stuck_recoveries 额度——用尽后即便还在"伪进展"也应该终止，
    而不是无限循环下去。"""
    agent = FakeAgent(outputs=[f"attempt {i}" for i in range(30)])
    cfg = _FakeCfg(
        tmp_path, max_rounds=30, consecutive_same_feedback_limit=100,
        max_stuck_recoveries=2,
        pseudo_progress_window=3, pseudo_progress_stagnation_threshold=0.15,
        pseudo_progress_max_score_cap=0.5,
    )
    spec = GoalSpec(goal_text="do X", acceptance_criteria=[f"c{i}" for i in range(1, 30)], confirmed=True)

    counter = {"i": 0}

    def fake_judge(**kw):
        counter["i"] += 1
        idx = counter["i"]
        checklist = [
            {"index": i, "passed": (i <= idx), "evidence": "微量进展"} for i in range(1, idx + 2)
        ]
        return _judge_json(
            "CONTINUE", feedback=f"微小进展 #{idx}",
            progress="SAME_APPROACH_NO_GAIN", progress_reason="每轮都只多过一条标准，没有实质推进",
            checklist=checklist,
        )

    monkeypatch.setattr("mini_agent.role_agents.goal_judge.run_goal_judge", lambda **kw: fake_judge(**kw))

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    result = runner.run()

    assert result.status == "stuck"
    # 恢复额度上限是 2，不应该跑到 max_rounds=30 才停
    assert result.rounds_used < 30


# ── §4 探索/收敛双模式（GoalPhase + 主动 compact，默认关闭）─────────────────

def test_goal_phase_starts_as_exploring(tmp_path):
    from mini_agent.role_agents.stuck_detector import GoalPhase
    agent = FakeAgent(outputs=["x"])
    cfg = _FakeCfg(tmp_path)
    spec = _confirmed_spec()
    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    assert runner._phase is GoalPhase.EXPLORING


def test_goal_phase_switches_to_converging_after_consecutive_positive_scores(tmp_path):
    from mini_agent.role_agents.stuck_detector import GoalPhase
    agent = FakeAgent(outputs=["x"])
    cfg = _FakeCfg(tmp_path, phase_convergence_window=3)
    spec = _confirmed_spec()
    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)

    runner._update_goal_phase({"progress_score": 0.3})
    assert runner._phase is GoalPhase.EXPLORING  # 只有 1 轮正向，还不够
    runner._update_goal_phase({"progress_score": 0.5})
    assert runner._phase is GoalPhase.EXPLORING  # 2 轮，还不够
    runner._update_goal_phase({"progress_score": 1.0})
    assert runner._phase is GoalPhase.CONVERGING  # 连续 3 轮正向，切换到收敛


def test_goal_phase_reverts_to_exploring_on_non_positive_score(tmp_path):
    from mini_agent.role_agents.stuck_detector import GoalPhase
    agent = FakeAgent(outputs=["x"])
    cfg = _FakeCfg(tmp_path, phase_convergence_window=2)
    spec = _confirmed_spec()
    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)

    runner._update_goal_phase({"progress_score": 0.5})
    runner._update_goal_phase({"progress_score": 0.5})
    assert runner._phase is GoalPhase.CONVERGING

    runner._update_goal_phase({"progress_score": 0.0})
    assert runner._phase is GoalPhase.EXPLORING  # 非正分立刻打回探索阶段

    # 缺失分数（None）也保守地视为未确认进展
    runner._update_goal_phase({"progress_score": 0.5})
    runner._update_goal_phase({"progress_score": 0.5})
    assert runner._phase is GoalPhase.CONVERGING
    runner._update_goal_phase({})
    assert runner._phase is GoalPhase.EXPLORING


def test_maybe_proactive_compact_disabled_by_default(tmp_path):
    """proactive_compact_enabled 默认 False，即使处于探索阶段也不应该触发。"""
    agent = FakeAgent(outputs=["x"])
    cfg = _FakeCfg(tmp_path)  # proactive_compact_enabled 默认 False
    spec = _confirmed_spec()
    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    runner._round = 10
    assert runner._maybe_proactive_compact() is False


def test_maybe_proactive_compact_triggers_at_interval_in_exploring_phase(tmp_path):
    agent = FakeAgent(outputs=["x"])
    cfg = _FakeCfg(tmp_path, proactive_compact_enabled=True, exploring_compact_interval=2)
    spec = _confirmed_spec()
    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)

    pin_calls = {"n": 0}
    monkeypatch_target = runner._pin_goal_context
    def fake_pin():
        pin_calls["n"] += 1
    runner._pin_goal_context = fake_pin

    # 阶段保持 EXPLORING（从不喂入正向分数）。
    triggered_rounds = []
    for r in range(1, 7):
        runner._round = r
        if runner._maybe_proactive_compact():
            triggered_rounds.append(r)

    # interval=2：第 2、4、6 轮应该触发（第 0 轮是初始基准）。
    assert triggered_rounds == [2, 4, 6]
    assert pin_calls["n"] == 3


def test_maybe_proactive_compact_paused_in_converging_phase(tmp_path):
    agent = FakeAgent(outputs=["x"])
    cfg = _FakeCfg(
        tmp_path, proactive_compact_enabled=True, exploring_compact_interval=1,
        phase_convergence_window=2,
    )
    spec = _confirmed_spec()
    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)

    # 连续 2 轮正向进展 → 切到 CONVERGING。
    runner._update_goal_phase({"progress_score": 0.5})
    runner._update_goal_phase({"progress_score": 0.5})
    from mini_agent.role_agents.stuck_detector import GoalPhase
    assert runner._phase is GoalPhase.CONVERGING

    runner._round = 5
    # 即使 interval=1（每轮都该触发），收敛阶段应该暂停主动触发。
    assert runner._maybe_proactive_compact() is False


def test_goal_runner_proactive_compact_disabled_matches_baseline_behavior(monkeypatch, tmp_path):
    """proactive_compact_enabled=False（默认）时，整条 run() 主循环的行为应该
    和升级前完全一致——本用例只是确认新增的旁路判断不会意外改变现有主循环
    （不会在不该 continue 的地方提前 continue）。"""
    agent = FakeAgent(outputs=["done"])
    cfg = _FakeCfg(tmp_path)
    spec = _confirmed_spec()

    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge",
        lambda **kw: _judge_json("DONE", feedback="全部完成"),
    )

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    result = runner.run()

    assert result.status == "done"
    assert result.rounds_used == 0


# ── §5 Goal 重规划提议 ───────────────────────────────────────────────────

_REPLAN_BLOCK = (
    "已经尝试了好几种办法，但是都卡在同一个地方。\n"
    "```replan_proposal\n"
    '{"suggested_split": ["先实现子功能A", "再实现子功能B"], '
    '"suggested_criteria_changes": ["把标准1放宽为覆盖80%场景即可"], '
    '"reason": "原验收标准依赖了一个不存在的前提"}\n'
    "```\n"
)


def test_replan_proposal_off_by_default_no_hint_no_parse(monkeypatch, tmp_path):
    """默认 replan_proposal_mode="off" 时，即使 agent 输出里恰好带了一个
    ```replan_proposal 代码块，也不应该被解析/展示——功能完全不生效。"""
    agent = FakeAgent(outputs=["attempt 1", _REPLAN_BLOCK, "attempt 3"])
    cfg = _FakeCfg(tmp_path, max_rounds=20, consecutive_same_feedback_limit=2, max_stuck_recoveries=1)
    spec = _confirmed_spec()

    same_feedback = "GOAL_STATUS: CONTINUE\n还是同样的问题"
    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge", lambda **kw: same_feedback,
    )

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    result = runner.run()

    assert result.status == "stuck"
    assert result.replan_proposal is None
    assert runner._awaiting_replan_proposal is False


def test_replan_proposal_confirm_mode_collects_but_does_not_apply(monkeypatch, tmp_path):
    """"confirm" 档位：最后一次恢复机会里请求提议、解析到非空提议后，
    只展示不自动改写 self._spec，goal_spec 应该仍然是原始版本。"""
    agent = FakeAgent(outputs=["attempt 1", "attempt 2", _REPLAN_BLOCK])
    cfg = _FakeCfg(
        tmp_path, max_rounds=20, consecutive_same_feedback_limit=2,
        max_stuck_recoveries=1, replan_proposal_mode="confirm",
    )
    spec = _confirmed_spec()

    same_feedback = "GOAL_STATUS: CONTINUE\n还是同样的问题"
    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge", lambda **kw: same_feedback,
    )

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    result = runner.run()

    assert result.status == "stuck"
    assert result.replan_proposal is not None
    assert result.replan_proposal["reason"] == "原验收标准依赖了一个不存在的前提"
    assert result.goal_spec.goal_text == "do the thing"  # 未被自动改写
    assert result.goal_spec.version == 1


def test_replan_proposal_auto_mode_applies_and_continues(monkeypatch, tmp_path):
    """"auto" 档位：解析到非空提议后自动调用 revise() 生成新版本并
    confirmed=True，替换 self._spec 后继续跑（不终止），最终能正常 DONE。"""
    agent = FakeAgent(outputs=["attempt 1", "attempt 2", _REPLAN_BLOCK, "attempt after replan"])
    cfg = _FakeCfg(
        tmp_path, max_rounds=20, consecutive_same_feedback_limit=2,
        max_stuck_recoveries=1, replan_proposal_mode="auto",
    )
    spec = _confirmed_spec()

    same_feedback = "GOAL_STATUS: CONTINUE\n还是同样的问题"
    responses = iter([same_feedback, same_feedback, "GOAL_STATUS: DONE\n全部通过"])
    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge", lambda **kw: next(responses),
    )

    def fake_revise(self, prior_spec, user_feedback):
        return GoalSpec(
            goal_text="拆分后的新目标",
            acceptance_criteria=["先完成子功能A"],
            version=prior_spec.version + 1,
        )

    monkeypatch.setattr(
        "mini_agent.goal_mode.spec.GoalSpecBuilder.revise", fake_revise,
    )

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    result = runner.run()

    assert result.status == "done"
    assert result.replan_proposal is None  # 已自动应用，不再展示为待确认
    assert result.goal_spec.goal_text == "拆分后的新目标"
    assert result.goal_spec.version == 2
    assert result.goal_spec.confirmed is True
    # 第三个输出（含 replan block）不会被送去 judge 评审（自动应用后直接
    # continue），所以 judge 总共只被调用 3 次：round1 CONTINUE、round2
    # CONTINUE(触发恢复)、以及应用新 spec 之后 round4(agent output "attempt
    # after replan") 才是 DONE。
    assert agent._call_idx == 4


def test_replan_proposal_auto_mode_applies_only_once_per_run(monkeypatch, tmp_path):
    """即使反复卡住、反复给出提议，"auto" 档位每次 run() 也只允许自动应用
    一次重规划，不会变成"每次卡住就自动放宽标准"的隐蔽漏洞。"""
    agent = FakeAgent(outputs=[
        "attempt 1", "attempt 2", _REPLAN_BLOCK,     # 触发第一次自动重规划
        "attempt 4", "attempt 5", _REPLAN_BLOCK,     # 再次卡住，但不应再自动应用
    ])
    cfg = _FakeCfg(
        tmp_path, max_rounds=20, consecutive_same_feedback_limit=2,
        max_stuck_recoveries=1, replan_proposal_mode="auto",
    )
    spec = _confirmed_spec()

    same_feedback = "GOAL_STATUS: CONTINUE\n还是同样的问题"
    monkeypatch.setattr(
        "mini_agent.role_agents.goal_judge.run_goal_judge", lambda **kw: same_feedback,
    )

    def fake_revise(self, prior_spec, user_feedback):
        return GoalSpec(
            goal_text="拆分后的新目标",
            acceptance_criteria=["先完成子功能A"],
            version=prior_spec.version + 1,
        )

    monkeypatch.setattr(
        "mini_agent.goal_mode.spec.GoalSpecBuilder.revise", fake_revise,
    )

    runner = GoalRunner(agent=agent, cfg=cfg, goal_spec=spec)
    result = runner.run()

    assert runner._replan_auto_applied is True
    # 第二次同样触发了"最后一次恢复机会"，也解析到了提议，但因为
    # _replan_auto_applied 已经是 True，不会再自动应用——最终仍然是 stuck
    # 终止（且展示的 replan_proposal 是第二次收集到的那份，因为"confirm"
    # 逻辑与"auto 已用尽"是独立的：mode=="auto" 时 _finish() 不展示，只有
    # "confirm" 档位才展示，这里保持 result.replan_proposal 存在但不会有
    # 第二次自动应用）。
    assert result.status == "stuck"
    assert result.goal_spec.goal_text == "拆分后的新目标"  # 仍是第一次应用后的版本
    assert result.goal_spec.version == 2
