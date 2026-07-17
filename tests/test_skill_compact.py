"""
tests/test_skill_compact.py — Skill 共享上下文压缩机制测试

覆盖：
  SkillUsageTracker:
    - record / touch / call_count
    - recent_names LRU 排序
    - build_compact_context 预算约束
    - 单 skill 截断（per_skill_tokens）
    - 总预算耗尽后丢弃较旧 skill
    - 从未追踪的 skill 追加到末尾
    - summary_lines 格式

  SkillLoader 集成:
    - activate 自动记录 tracker
    - auto_activate 自动记录 tracker
    - build_context 每次记录 tracker
    - build_compact_context 委托 tracker
    - include_inactive 参数

  Agent._build_skill_compact_block:
    - 有 skill 时返回非空
    - 无 skill_loader 时返回空
    - dropped 时打印 warning

  Agent.compact_with_skills:
    - 历史被替换为摘要 + skill 块
    - 无 skill 时仍能完成（只有摘要）
"""

from __future__ import annotations

import sys
import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

import pytest
from mini_agent.skills import Skill, SkillLoader
from mini_agent.skills.tracker import SkillUsageTracker, SkillCallRecord


# ── 共用工厂 ──────────────────────────────────────────────────────────────────

def make_tracker(per_skill=5_000, total=25_000) -> SkillUsageTracker:
    return SkillUsageTracker(per_skill_tokens=per_skill, total_budget=total)


def make_loader(skill_defs: list[dict], per_skill=5_000, total=25_000) -> SkillLoader:
    loader = SkillLoader.__new__(SkillLoader)
    loader._dirs   = []
    loader._all    = {}
    loader._active = []
    loader._loaded_resources = {}
    loader.tracker = SkillUsageTracker(per_skill_tokens=per_skill, total_budget=total)
    from mini_agent.skills.usage_detector import SkillUsageDetector
    loader.detector = SkillUsageDetector()
    for d in skill_defs:
        content = d.get("content", f"# {d['name']}\n" + "x" * d.get("content_len", 100))
        skill = Skill(
            name         = d["name"],
            description  = d.get("description", f"Skill {d['name']}"),
            location     = Path(f"/fake/{d['name']}/SKILL.md"),
            content      = content,
            trigger_words= d.get("trigger_words", [d["name"].lower()]),
        )
        loader._all[skill.name] = skill
    # 在所有 skill 加入后再构建指纹
    loader.detector.build_fingerprints(loader._all)
    return loader


# ── SkillUsageTracker ─────────────────────────────────────────────────────────

class TestSkillUsageTracker:

    def test_record_increments_call_count(self):
        t = make_tracker()
        t.record("docx")
        t.record("docx")
        assert t.get_record("docx").call_count == 2

    def test_record_updates_last_called(self):
        t = make_tracker()
        before = time.time()
        t.record("docx")
        after = time.time()
        assert before <= t.get_record("docx").last_called <= after

    def test_recent_names_lru_order(self):
        t = make_tracker()
        t.record("a")
        time.sleep(0.01)
        t.record("b")
        time.sleep(0.01)
        t.record("a")   # a 最近用
        assert t.recent_names()[0] == "a"
        assert t.recent_names()[1] == "b"

    def test_forget_removes_record(self):
        t = make_tracker()
        t.record("docx")
        t.forget("docx")
        assert t.get_record("docx") is None

    def test_clear_empties_all(self):
        t = make_tracker()
        t.record("a"); t.record("b")
        t.clear()
        assert t.records == []

    def test_build_compact_context_returns_all_within_budget(self):
        t = make_tracker(per_skill=1_000, total=10_000)
        t.record("a")
        t.record("b")
        contents = {"a": "A" * 100, "b": "B" * 100}   # 각 25 tokens
        text, included, dropped = t.build_compact_context(contents)
        assert "a" in included
        assert "b" in included
        assert dropped == []
        assert "A" * 100 in text
        assert "B" * 100 in text

    def test_build_compact_context_clips_per_skill(self):
        # 需要 2 个 skill，普通 skill 才会受 per_skill 截断
        # per_skill = 10 tokens = 40 chars；total 足够
        t = make_tracker(per_skill=10, total=10_000)
        t.record("big")
        t.record("small")
        contents = {
            "big":   "X" * 400,   # 100 tokens >> 10 limit，普通 skill 会被截断
            "small": "Y" * 20,    # 5 tokens，不会被截断
        }
        # big 不在 protected，小 skill 也不在 protected → 均受截断规则约束
        text, included, dropped = t.build_compact_context(contents, protected=set())
        assert "big" in included
        assert "omitted" in text   # 截断提示出现

    def test_build_compact_context_drops_when_total_budget_exceeded(self):
        # total = 50 tokens = 200 chars; per_skill = 30 tokens
        t = make_tracker(per_skill=30, total=50)
        t.record("first")
        time.sleep(0.01)
        t.record("second")   # second 更新，优先级更高
        # second 占 ~30 tokens；first 再加 30 > 50 budget
        contents = {
            "first":  "F" * 120,   # ~30 tokens
            "second": "S" * 120,   # ~30 tokens
        }
        text, included, dropped = t.build_compact_context(contents)
        # second 更近，先填；first 超出预算被丢弃
        assert "second" in included
        assert "first" in dropped

    def test_untracked_skills_appended_last(self):
        t = make_tracker(per_skill=1_000, total=100_000)
        t.record("tracked")
        contents = {"tracked": "T" * 40, "untracked": "U" * 40}
        text, included, dropped = t.build_compact_context(contents)
        assert included.index("tracked") < included.index("untracked")

    def test_empty_content_dict_returns_empty(self):
        t = make_tracker()
        text, included, dropped = t.build_compact_context({})
        assert text == ""
        assert included == []

    def test_summary_lines_format(self):
        t = make_tracker()
        assert "(no skills" in t.summary_lines()[0]
        t.record("docx")
        lines = t.summary_lines()
        assert any("docx" in l for l in lines)
        assert any("calls=" in l for l in lines)


# ── SkillLoader 集成 ──────────────────────────────────────────────────────────

class TestSkillLoaderAutoUnloadIdle:
    """SkillLoader.auto_unload_idle() — compact 时自动卸载长期未用 skill。"""

    def test_never_used_skill_gets_unloaded(self):
        loader = make_loader([{"name": "docx"}, {"name": "pptx"}])
        loader.activate("docx")
        loader.activate("pptx")
        # 从未调用 record_usage → tracker 无记录 → 应被卸载
        unloaded = loader.auto_unload_idle(idle_seconds=1800)
        assert set(unloaded) == {"docx", "pptx"}
        assert loader.active == []

    def test_recently_used_skill_survives(self):
        loader = make_loader([{"name": "docx"}])
        loader.activate("docx")
        loader.tracker.record("docx")
        unloaded = loader.auto_unload_idle(idle_seconds=1800)
        assert unloaded == []
        assert "docx" in loader.active

    def test_stale_skill_gets_unloaded(self):
        loader = make_loader([{"name": "docx"}])
        loader.activate("docx")
        loader.tracker.record("docx")
        # 手动把 last_called 拨回很久以前
        loader.tracker.get_record("docx").last_called = time.time() - 999_999
        unloaded = loader.auto_unload_idle(idle_seconds=1800)
        assert unloaded == ["docx"]
        assert loader.active == []

    def test_protect_set_is_respected(self):
        loader = make_loader([{"name": "docx"}, {"name": "pptx"}])
        loader.activate("docx")
        loader.activate("pptx")
        unloaded = loader.auto_unload_idle(idle_seconds=1800, protect={"docx"})
        assert unloaded == ["pptx"]
        assert loader.active == ["docx"]

    def test_inactive_skill_is_not_affected(self):
        loader = make_loader([{"name": "docx"}])
        # never activated
        unloaded = loader.auto_unload_idle(idle_seconds=1800)
        assert unloaded == []


class TestSkillLoaderTrackerIntegration:

    def test_activate_does_not_record_tracker(self):
        """激活 skill 不再更新 tracker（只有实际使用才更新）。"""
        loader = make_loader([{"name": "docx"}])
        loader.activate("docx")
        assert loader.tracker.get_record("docx") is None

    def test_auto_activate_does_not_record_tracker(self):
        """auto_activate 也不更新 tracker。"""
        loader = make_loader([{"name": "docx", "trigger_words": ["word"]}])
        loader.auto_activate("create a word document")
        assert loader.tracker.get_record("docx") is None

    def test_build_context_does_not_record_tracker(self):
        """build_context 不更新 tracker，只有 record_usage 才更新。"""
        loader = make_loader([{"name": "docx"}])
        loader._active = ["docx"]
        loader.build_context()
        assert loader.tracker.get_record("docx") is None

    def test_record_usage_updates_tracker(self):
        """record_usage 在检测到实际使用时才更新 tracker。"""
        loader = make_loader([{
            "name": "docx",
            "content": "python-docx ParagraphStyle DocumentObject WordTable " * 5,
        }])
        loader._active = ["docx"]
        used = loader.record_usage("I used python-docx with ParagraphStyle to create a document.")
        assert "docx" in used
        assert loader.tracker.get_record("docx") is not None
        assert loader.tracker.get_record("docx").call_count == 1

    def test_record_usage_multiple_calls_increment_count(self):
        loader = make_loader([{
            "name": "docx",
            "content": "python-docx ParagraphStyle DocumentObject WordTable " * 5,
        }])
        loader._active = ["docx"]
        response = "Used python-docx and ParagraphStyle for the document."
        loader.record_usage(response)
        loader.record_usage(response)
        assert loader.tracker.get_record("docx").call_count == 2

    def test_build_compact_context_active_only(self):
        loader = make_loader([
            {"name": "docx", "content": "python-docx " * 20},
            {"name": "pdf",  "content": "reportlab " * 20},
        ])
        loader.activate("docx")
        loader.activate("pdf")
        # 给两个 skill 都记录使用（compact 时才有内容）
        loader.tracker.record("docx")
        loader.tracker.record("pdf")
        text, included, dropped = loader.build_compact_context(include_inactive=False)
        assert set(included) == {"docx", "pdf"}

    def test_build_compact_context_include_inactive(self):
        loader = make_loader([
            {"name": "docx", "content": "python-docx " * 20},
            {"name": "pdf",  "content": "reportlab " * 20},
        ])
        loader.activate("docx")
        loader.activate("pdf")
        loader.deactivate("pdf")
        # 手动给两个都打 tracker 记录（模拟曾经使用过）
        loader.tracker.record("docx")
        loader.tracker.record("pdf")
        text, included, dropped = loader.build_compact_context(include_inactive=True)
        assert "docx" in included
        assert "pdf" in included

    def test_budget_params_passed_to_tracker(self):
        loader = make_loader([], per_skill=1_234, total=9_876)
        assert loader.tracker.per_skill_tokens == 1_234
        assert loader.tracker.total_budget     == 9_876


# ── LRU 淘汰顺序 ─────────────────────────────────────────────────────────────

class TestLRUEviction:
    """验证较早调用的 skill 在预算耗尽时被完全丢弃。"""

    def test_most_recent_survives_when_budget_tight(self):
        # 每个 skill 需要 ~25 tokens；budget 只够 1 个
        per = 25; total = 30
        t = make_tracker(per_skill=per, total=total)
        t.record("old_skill")
        time.sleep(0.01)
        t.record("new_skill")
        contents = {
            "old_skill": "O" * (per * 4),
            "new_skill": "N" * (per * 4),
        }
        _, included, dropped = t.build_compact_context(contents)
        assert "new_skill" in included
        assert "old_skill" in dropped

    def test_all_dropped_when_budget_zero(self):
        # 2 个 skill，均不在 protected，budget=0 → 两个都应被 drop
        t = make_tracker(per_skill=100, total=0)
        t.record("a")
        t.record("b")
        contents = {"a": "A" * 40, "b": "B" * 40}
        _, included, dropped = t.build_compact_context(contents, protected=set())
        assert included == []
        assert set(dropped) == {"a", "b"}


# ── Agent 集成（stub） ────────────────────────────────────────────────────────

class TestAgentSkillCompact:

    def _make_agent(self, skill_defs=None, per_skill=5_000, total=25_000):
        loader = make_loader(skill_defs or [], per_skill=per_skill, total=total)

        cfg = MagicMock()
        cfg.skill_compact_budget     = total
        cfg.skill_compact_per_skill  = per_skill
        cfg.auto_save_session        = False
        cfg.skill_chunking_enabled   = False
        cfg.system_extra             = ""
        cfg.sandbox                  = False
        cfg.claude_md_content        = ""
        cfg.agent_name               = "test"

        from mini_agent.config import SessionStats
        from mini_agent.agent import Agent
        agent = Agent.__new__(Agent)
        agent.cfg          = cfg
        agent.skill_loader = loader
        agent._history     = []

        from mini_agent.history_manager import HistoryManager
        agent._hist          = HistoryManager(cfg=agent.cfg, skill_loader=getattr(agent, 'skill_loader', None))
        agent._hist._history = agent._history  # 共享同一列表
        agent._project_snapshot = None
        agent._memory        = None
        agent.stats          = SessionStats()
        agent._turn_snapshot = None
        return agent

    def test_build_skill_compact_block_empty_without_loader(self):
        agent = self._make_agent()
        agent.skill_loader = None
        with patch("mini_agent.ui.renderer.print_info"), patch("mini_agent.ui.renderer.print_warning"):
            result = agent._build_skill_compact_block()
        assert result == ""

    def test_build_skill_compact_block_empty_when_no_calls(self):
        agent = self._make_agent([{"name": "docx"}])
        # 没有调用过任何 skill，tracker 无记录
        with patch("mini_agent.ui.renderer.print_info"), patch("mini_agent.ui.renderer.print_warning"):
            result = agent._build_skill_compact_block()
        assert result == ""

    def test_build_skill_compact_block_returns_content_after_activate(self):
        agent = self._make_agent([{"name": "docx", "content": "# DocX\nsome content here docx"}])
        agent.skill_loader.activate("docx")
        # 模拟实际使用：手动打 tracker 记录（真实场景由 record_usage 完成）
        agent.skill_loader.tracker.record("docx")
        with patch("mini_agent.ui.renderer.print_info"), patch("mini_agent.ui.renderer.print_warning"):
            result = agent._build_skill_compact_block()
        assert "DocX" in result or "docx" in result.lower()

    def test_build_skill_compact_block_warns_on_dropped(self):
        # a 是 active（受保护，全文强制写入），b 是 inactive（不受保护）
        # budget 设为 a 全文的 token 量，使 b 恰好放不下
        # a 内容 = 120 chars → ~30 tokens；budget = 30，per_skill = 200
        agent = self._make_agent(
            [
                {"name": "a", "content": "A" * 120},
                {"name": "b", "content": "B" * 500},
            ],
            per_skill=200, total=30,
        )
        # 只激活 a；b 有 tracker 记录但不是 active
        agent.skill_loader.activate("a")
        agent.skill_loader.tracker.record("b")   # b 曾被使用过
        agent.skill_loader.tracker.record("a")   # a 也有记录

        warnings = []
        with patch("mini_agent.ui.renderer.print_info"), \
             patch("mini_agent.ui.renderer.print_warning", side_effect=lambda m: warnings.append(m)):
            agent._build_skill_compact_block()
        assert any("drop" in w.lower() or "budget" in w.lower() for w in warnings)

    def test_compact_with_skills_replaces_history(self):
        agent = self._make_agent([{"name": "docx"}])
        agent.skill_loader.activate("docx")
        agent._history = [
            {"role": "user",      "content": "old turn 1"},
            {"role": "assistant", "content": "old reply 1"},
            {"role": "user",      "content": "old turn 2"},
            {"role": "assistant", "content": "old reply 2"},
        ]

        def fake_run_turn(msg):
            agent._history.append({"role": "user", "content": msg})
            agent._history.append({"role": "assistant", "content": "SUMMARY"})
            return "SUMMARY"

        agent.run_turn = fake_run_turn

        with patch("mini_agent.ui.renderer.print_info"), patch("mini_agent.ui.renderer.print_success"), \
             patch("mini_agent.ui.renderer.print_warning"):
            agent.compact_with_skills()

        # 历史应该被替换为摘要 + (可能的 skill 块)
        roles = [m["role"] for m in agent._history]
        assert "user" in roles
        assert "assistant" in roles
        # 原有的长历史已被压缩
        contents = [m["content"] for m in agent._history]
        assert "SUMMARY" in contents
        assert "old turn 1" not in contents

    def test_compact_with_skills_empty_history(self):
        agent = self._make_agent()
        agent._history = []
        with patch("mini_agent.ui.renderer.print_info"):
            result = agent.compact_with_skills()
        assert result == ""
        assert agent._history == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])


# ── 保护机制专项测试 ───────────────────────────────────────────────────────────

class TestProtectionMechanism:
    """验证 protected set 和单 skill 豁免的完整行为。"""

    # ── per_skill 截断豁免 ────────────────────────────────────────────────────

    def test_protected_skill_not_clipped(self):
        """受保护的 skill 超过 per_skill_tokens 也不截断。"""
        t = make_tracker(per_skill=10, total=100_000)  # per_skill 极小
        t.record("docx")
        long_content = "X" * 800   # 200 tokens >> per_skill=10
        _, included, _ = t.build_compact_context(
            {"docx": long_content},
            protected={"docx"},
        )
        # 应该被写入，且不含截断提示
        assert "docx" in included

    def test_protected_skill_content_fully_included(self):
        """受保护 skill 的全文（不截断）出现在输出中。"""
        t = make_tracker(per_skill=5, total=100_000)
        t.record("docx")
        full_content = "FULL_CONTENT_" * 50  # 远超 per_skill=5
        text, _, _ = t.build_compact_context(
            {"docx": full_content},
            protected={"docx"},
        )
        assert "FULL_CONTENT_" * 50 in text  # 全文出现，未被截断

    def test_unprotected_skill_still_clipped(self):
        """未受保护的 skill 超过 per_skill_tokens 会被截断。"""
        t = make_tracker(per_skill=10, total=100_000)
        t.record("active_skill")
        t.record("old_skill")
        contents = {
            "active_skill": "A" * 40,    # 10 tokens，恰好不截
            "old_skill":    "O" * 400,   # 100 tokens，超出 per_skill=10
        }
        text, included, _ = t.build_compact_context(
            contents,
            protected={"active_skill"},
        )
        assert "old_skill" in included
        assert "omitted" in text   # old_skill 被截断

    # ── total_budget 豁免 ─────────────────────────────────────────────────────

    def test_protected_skill_survives_exhausted_budget(self):
        """预算已被普通 skill 耗尽，受保护的 skill 仍然强制写入。"""
        # budget=50 tokens；先写 normal_skill 耗完预算，再写 active_skill
        # 由于 active_skill 受保护，即使预算不足也要写入
        t = make_tracker(per_skill=50, total=50)
        # normal_skill LRU 排名更高（先调用），active_skill 更晚
        t.record("normal_skill")
        time.sleep(0.01)
        t.record("active_skill")   # active_skill 更新，LRU rank=1，先处理
        # 但保护 skill 在第一轮处理，normal_skill 在第二轮
        contents = {
            "normal_skill":  "N" * 200,   # 50 tokens，恰好耗尽 budget
            "active_skill":  "A" * 200,   # 50 tokens
        }
        _, included, dropped = t.build_compact_context(
            contents,
            protected={"active_skill"},
        )
        # active_skill 受保护，必须包含
        assert "active_skill" in included
        # normal_skill 不受保护，budget 耗尽后被 drop
        assert "normal_skill" in dropped

    def test_multiple_protected_skills_all_survive(self):
        """多个受保护 skill 全部写入，不互相竞争预算。"""
        t = make_tracker(per_skill=10, total=20)  # 预算只够 20 tokens
        t.record("skill_a"); t.record("skill_b"); t.record("skill_c")
        contents = {
            "skill_a": "A" * 200,  # 50 tokens
            "skill_b": "B" * 200,  # 50 tokens
            "skill_c": "C" * 40,   # 10 tokens（普通 skill）
        }
        _, included, dropped = t.build_compact_context(
            contents,
            protected={"skill_a", "skill_b"},
        )
        assert "skill_a" in included
        assert "skill_b" in included
        # skill_c 是普通 skill，budget 已被保护 skill 占用，被 drop
        assert "skill_c" in dropped

    # ── 单 skill 豁免 ─────────────────────────────────────────────────────────

    def test_single_skill_not_clipped_even_without_protection(self):
        """只有一个 skill 时，即使不在 protected 中，也不截断。"""
        t = make_tracker(per_skill=5, total=100_000)
        t.record("solo")
        long_content = "S" * 800  # 200 tokens >> per_skill=5
        text, included, dropped = t.build_compact_context(
            {"solo": long_content},
            protected=set(),  # 空保护集
        )
        assert "solo" in included
        assert dropped == []
        assert "omitted" not in text   # 未被截断

    def test_single_skill_survives_zero_budget(self):
        """只有一个 skill 时，即使 budget=0 也写入（单 skill 豁免）。"""
        t = make_tracker(per_skill=10, total=0)
        t.record("solo")
        text, included, dropped = t.build_compact_context(
            {"solo": "content"},
            protected=set(),
        )
        assert "solo" in included
        assert dropped == []

    def test_two_skills_no_exemption(self):
        """两个 skill 时单 skill 豁免不生效，截断和预算规则正常工作。"""
        t = make_tracker(per_skill=5, total=100_000)
        t.record("a"); t.record("b")
        contents = {
            "a": "A" * 200,  # 50 tokens >> per_skill=5
            "b": "B" * 200,
        }
        text, included, _ = t.build_compact_context(contents, protected=set())
        # 两个都不在 protected，per_skill 截断生效
        assert "omitted" in text

    # ── header 标注 ──────────────────────────────────────────────────────────

    def test_header_notes_protection(self):
        """header 中标注了受保护的 skill 名称。"""
        t = make_tracker(per_skill=5, total=100_000)
        t.record("docx")
        text, _, _ = t.build_compact_context(
            {"docx": "content"},
            protected={"docx"},
        )
        assert "protected" in text
        assert "docx" in text

    def test_header_notes_single_skill_exemption(self):
        """只有一个 skill 时 header 标注 single-skill exemption。"""
        t = make_tracker(per_skill=5, total=100_000)
        t.record("solo")
        text, _, _ = t.build_compact_context(
            {"solo": "content"},
            protected=set(),
        )
        assert "single-skill exemption" in text

    def test_header_no_protection_note_when_none(self):
        """无保护 skill（且有多个 skill）时 header 不出现 protected 字样。"""
        t = make_tracker(per_skill=5_000, total=100_000)
        t.record("a"); t.record("b")
        text, _, _ = t.build_compact_context(
            {"a": "short", "b": "short"},
            protected=set(),
        )
        assert "protected" not in text

    # ── SkillLoader 集成：active skill 自动成为 protected ────────────────────

    def test_loader_active_skills_are_protected(self):
        """SkillLoader.build_compact_context 自动将 active skill 作为 protected。"""
        loader = make_loader([
            {"name": "active",  "content": "A" * 800},   # 远超 per_skill
            {"name": "inactive","content": "B" * 800},
        ], per_skill=10, total=100_000)
        loader.activate("active")
        # 通过 tracker 模拟两者都曾被使用过（inactive 较早使用）
        loader.tracker.record("inactive")
        time.sleep(0.01)
        loader.tracker.record("active")

        text, included, _ = loader.build_compact_context(include_inactive=True)
        # active skill 受保护，全文不截断
        assert "active" in included
        assert "A" * 800 in text

    def test_loader_inactive_skill_gets_clipped(self):
        """非 active 的 skill 仍然受截断约束。"""
        loader = make_loader([
            {"name": "active",  "content": "A" * 40},
            {"name": "inactive","content": "B" * 800},   # 远超 per_skill
        ], per_skill=10, total=100_000)
        loader.activate("active")
        # 两者都有使用记录
        loader.tracker.record("inactive")
        time.sleep(0.01)
        loader.tracker.record("active")

        text, included, _ = loader.build_compact_context(include_inactive=True)
        assert "inactive" in included
        assert "omitted" in text   # inactive 被截断


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
