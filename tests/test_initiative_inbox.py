"""tests/test_initiative_inbox.py

覆盖 next_doc/initiative_systems_unification_plan.md 阶段一：
`perception/initiative_inbox.py::initiative_inbox_snapshot()` 的只读
聚合逻辑——三路来源（growth_advisor / capability_learning /
soft_goal_deriver）各自的候选能否被正确映射到统一的 domain，以及
domain 过滤、异常隔离两个关键行为。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.evolution.capability_learning import (
    CapabilityOutlineSuggestionStore,
    CapabilityQuestionStore,
    OutlineSuggestion,
)
from mini_agent.evolution.growth_advisor import GrowthBacklog
from mini_agent.perception.goal_backlog import GoalBacklog
from mini_agent.perception.initiative_inbox import (
    DOMAIN_AGENT_BEHAVIOR,
    DOMAIN_AGENT_KNOWLEDGE,
    DOMAIN_USER_GROWTH,
    initiative_inbox_snapshot,
)
from mini_agent.storage.paths import AgentPaths


class InitiativeInboxTests(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_empty_state_returns_empty_snapshot(self):
        snap = initiative_inbox_snapshot(self.paths)
        self.assertEqual(snap["total"], 0)
        self.assertEqual(snap["items"], [])
        for d in (DOMAIN_USER_GROWTH, DOMAIN_AGENT_KNOWLEDGE, DOMAIN_AGENT_BEHAVIOR):
            self.assertEqual(snap["counts_by_domain"][d], 0)

    def test_aggregates_all_three_sources(self):
        # growth_advisor: 一条 pending 候选
        backlog = GrowthBacklog(self.paths)
        backlog.add_or_merge(
            title="学习数据可视化",
            rationale="最近多次提到想做图表分析",
            evidence_refs=["mem1", "mem2", "mem3"],
            min_evidence_count=1,
            max_pending=10,
            dismissed_cooldown_days=30,
        )

        # capability_learning: 一条待回答问题 + 一条大纲建议
        q_store = CapabilityQuestionStore(self.paths)
        q_store.raise_question("track1", "topic1", "你更关注哪个细分方向？")

        s_store = CapabilityOutlineSuggestionStore(self.paths)
        s_store.add(
            OutlineSuggestion(
                suggestion_id="sug1",
                track_id="track1",
                source_question_id="capq_x",
                suggested_name="新的子主题",
                rationale="用户回答里反复提到",
            )
        )

        # soft_goal_deriver: 一个尚未被处理的 agent_derived Goal
        gb = GoalBacklog(self.paths)
        gb.add_goal(
            title="补齐可视化能力短板",
            description="capability_map 显示置信度偏低",
            source="agent_derived",
        )
        # 用户已经手动创建、非 agent_derived 的 Goal 不应该出现在收件箱里
        gb.add_goal(title="用户自己建的目标", source="user")

        snap = initiative_inbox_snapshot(self.paths)
        self.assertEqual(snap["total"], 4)
        self.assertEqual(snap["counts_by_domain"][DOMAIN_USER_GROWTH], 1)
        self.assertEqual(snap["counts_by_domain"][DOMAIN_AGENT_KNOWLEDGE], 2)
        self.assertEqual(snap["counts_by_domain"][DOMAIN_AGENT_BEHAVIOR], 1)

        titles = {it["title"] for it in snap["items"]}
        self.assertIn("学习数据可视化", titles)
        self.assertIn("你更关注哪个细分方向？", titles)
        self.assertIn("新的子主题", titles)
        self.assertIn("补齐可视化能力短板", titles)
        self.assertNotIn("用户自己建的目标", titles)

    def test_domain_filter_skips_unrequested_sources(self):
        backlog = GrowthBacklog(self.paths)
        backlog.add_or_merge(
            title="A 方向",
            rationale="r",
            evidence_refs=["mem1"],
            min_evidence_count=1,
            max_pending=10,
            dismissed_cooldown_days=30,
        )
        q_store = CapabilityQuestionStore(self.paths)
        q_store.raise_question("track1", "topic1", "问题？")

        snap = initiative_inbox_snapshot(self.paths, domains=[DOMAIN_USER_GROWTH])
        self.assertEqual(snap["total"], 1)
        self.assertEqual(snap["items"][0]["domain"], DOMAIN_USER_GROWTH)

    def test_touched_agent_derived_goal_not_shown_as_pending(self):
        gb = GoalBacklog(self.paths)
        node = gb.add_goal(title="已经被处理过的软目标", source="agent_derived")
        # 模拟执行引擎/用户已经碰过这个节点：直接落盘一个明显滞后的
        # last_touched_at（`update_fields()` 内部会强制把它改写成
        # "此刻"，不适合用来模拟"过去某个时间点被碰过"，这里绕开它直接
        # 操作内存态 + save()，等价于磁盘上已经是这个状态）。
        gb._nodes[node.id].last_touched_at = node.created_at + 3600
        gb.save()

        snap = initiative_inbox_snapshot(self.paths, domains=[DOMAIN_AGENT_BEHAVIOR])
        self.assertEqual(snap["total"], 0)

    def test_broken_single_source_does_not_break_others(self):
        # capability_learning 的存储路径写入非法 jsonl，触发内部读取异常，
        # 应该只影响这一路，不影响 growth_advisor 那一路的结果。
        backlog = GrowthBacklog(self.paths)
        backlog.add_or_merge(
            title="仍然应该出现",
            rationale="r",
            evidence_refs=["mem1"],
            min_evidence_count=1,
            max_pending=10,
            dismissed_cooldown_days=30,
        )
        self.paths.capability_questions_path.parent.mkdir(parents=True, exist_ok=True)
        self.paths.capability_questions_path.write_text("not json\n", encoding="utf-8")

        snap = initiative_inbox_snapshot(self.paths)
        titles = {it["title"] for it in snap["items"]}
        self.assertIn("仍然应该出现", titles)


if __name__ == "__main__":
    unittest.main()
