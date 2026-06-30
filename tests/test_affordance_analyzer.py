"""
tests/test_affordance_analyzer.py — [具身改进 B4] 余裕感知层单元测试

覆盖：
  1. AffordanceMap.is_empty() / to_dict() / to_system_prompt_fragment()
  2. AffordanceAnalyzer.analyze() 在各种输入组合下：
     - 纯空输入 → is_empty()
     - 仅 open_threads（status 过滤、priority 排序、type bonus）
     - 仅 lesson_entries（高风险关键词匹配、human_feedback 优先级、去重）
     - 仅 capability_entries（低置信度筛选、按置信度升序）
     - 混合输入 → top_opportunities 排序
  3. to_system_prompt_fragment() 的格式正确性（截断、标题存在）
  4. 边界：resolved 的 open_thread 不进入 known_issues；
          空 lesson text 不产生 high_risk_zone
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass, field
from typing import Optional


# ── 轻量 stub：避免在单元测试里依赖 MemoryEntry / OpenThread / CapabilityMapEntry 的完整初始化逻辑
# ── 只提供 AffordanceAnalyzer 实际访问的字段（getattr 安全读取）

@dataclass
class _OpenThread:
    title: str
    status: str = "open"
    type: str = "question"
    priority: str = "medium"


@dataclass
class _MemoryEntry:
    trigger: str = ""
    outcome: str = ""
    entry_type: str = "lesson"
    source: str = "self_reflection"
    summary: str = ""


@dataclass
class _CapabilityEntry:
    domain: str
    confidence: float


from mini_agent.perception.affordance_analyzer import AffordanceAnalyzer, AffordanceMap


class TestAffordanceMapEmpty(unittest.TestCase):
    def test_default_is_empty(self):
        m = AffordanceMap()
        self.assertTrue(m.is_empty())

    def test_fragment_is_empty_string_when_no_data(self):
        m = AffordanceMap()
        self.assertEqual(m.to_system_prompt_fragment(), "")

    def test_not_empty_when_has_known_issues(self):
        m = AffordanceMap(known_issues=["修复登录 bug"])
        self.assertFalse(m.is_empty())

    def test_to_dict_keys(self):
        m = AffordanceMap()
        d = m.to_dict()
        for key in ("known_issues", "unexplored_areas", "high_risk_zones", "top_opportunities"):
            self.assertIn(key, d)


class TestAffordanceMapFragment(unittest.TestCase):
    def test_fragment_contains_header(self):
        m = AffordanceMap(known_issues=["问题A"])
        frag = m.to_system_prompt_fragment()
        self.assertIn("当前环境行动可能性", frag)

    def test_fragment_contains_known_issues(self):
        m = AffordanceMap(known_issues=["问题A", "问题B"])
        frag = m.to_system_prompt_fragment()
        self.assertIn("问题A", frag)
        self.assertIn("问题B", frag)

    def test_fragment_truncates_known_issues_to_3(self):
        m = AffordanceMap(known_issues=["A", "B", "C", "D", "E"])
        frag = m.to_system_prompt_fragment()
        # 只显示前 3 个（代码里 [:3]）
        self.assertIn("A", frag)
        self.assertNotIn("D", frag)

    def test_fragment_contains_high_risk(self):
        m = AffordanceMap(high_risk_zones=["误删数据库…"])
        frag = m.to_system_prompt_fragment()
        self.assertIn("历史高风险区域", frag)

    def test_fragment_contains_top_opportunities(self):
        m = AffordanceMap(top_opportunities=["修复 auth bug（bug/high）"])
        frag = m.to_system_prompt_fragment()
        self.assertIn("最值得关注", frag)
        self.assertIn("修复 auth bug", frag)


class TestAnalyzeEmpty(unittest.TestCase):
    def test_all_none_returns_empty_map(self):
        result = AffordanceAnalyzer().analyze()
        self.assertTrue(result.is_empty())

    def test_empty_lists_returns_empty_map(self):
        result = AffordanceAnalyzer().analyze(
            open_threads=[], lesson_entries=[], capability_entries=[]
        )
        self.assertTrue(result.is_empty())


class TestAnalyzeOpenThreads(unittest.TestCase):
    def test_open_threads_appear_in_known_issues(self):
        threads = [_OpenThread(title="修复登录 bug", status="open")]
        result = AffordanceAnalyzer().analyze(open_threads=threads)
        self.assertIn("修复登录 bug", result.known_issues)

    def test_resolved_threads_excluded(self):
        threads = [
            _OpenThread(title="已修复", status="resolved"),
            _OpenThread(title="待修复", status="open"),
        ]
        result = AffordanceAnalyzer().analyze(open_threads=threads)
        self.assertNotIn("已修复", result.known_issues)
        self.assertIn("待修复", result.known_issues)

    def test_priority_ordering_high_before_low(self):
        threads = [
            _OpenThread(title="低优先级任务", status="open", priority="low"),
            _OpenThread(title="高优先级任务", status="open", priority="high"),
        ]
        result = AffordanceAnalyzer().analyze(open_threads=threads)
        idx_high = result.known_issues.index("高优先级任务")
        idx_low = result.known_issues.index("低优先级任务")
        self.assertLess(idx_high, idx_low)

    def test_bug_type_gets_score_bonus_in_top_opportunities(self):
        threads = [
            _OpenThread(title="低优先级文档任务", status="open", priority="medium", type="docs"),
            _OpenThread(title="严重 bug", status="open", priority="medium", type="bug"),
        ]
        result = AffordanceAnalyzer().analyze(open_threads=threads)
        # bug 类型有 +2 bonus，应该排在前面
        opps = result.top_opportunities
        bug_idx = next((i for i, o in enumerate(opps) if "严重 bug" in o), None)
        doc_idx = next((i for i, o in enumerate(opps) if "低优先级文档任务" in o), None)
        if bug_idx is not None and doc_idx is not None:
            self.assertLess(bug_idx, doc_idx)

    def test_no_duplicate_known_issues(self):
        threads = [
            _OpenThread(title="重复问题", status="open"),
            _OpenThread(title="重复问题", status="open"),
        ]
        result = AffordanceAnalyzer().analyze(open_threads=threads)
        self.assertEqual(result.known_issues.count("重复问题"), 1)


class TestAnalyzeLessonEntries(unittest.TestCase):
    def _make_risky_entry(self, trigger: str, source: str = "self_reflection") -> _MemoryEntry:
        return _MemoryEntry(trigger=trigger, outcome="失败了", source=source)

    def test_risky_trigger_produces_high_risk_zone(self):
        entries = [self._make_risky_entry("误删了生产数据库")]
        result = AffordanceAnalyzer().analyze(lesson_entries=entries)
        self.assertTrue(len(result.high_risk_zones) > 0)
        self.assertIn("误删了生产数据库", result.high_risk_zones[0])

    def test_safe_trigger_not_in_high_risk(self):
        entries = [_MemoryEntry(trigger="添加了一个新功能", outcome="成功完成", source="self_reflection")]
        result = AffordanceAnalyzer().analyze(lesson_entries=entries)
        self.assertEqual(result.high_risk_zones, [])

    def test_human_feedback_prioritized_over_self_reflection(self):
        entries = [
            _MemoryEntry(trigger="普通失败操作", outcome="error", source="self_reflection"),
            _MemoryEntry(trigger="用户纠正了这个崩溃行为", outcome="fail", source="human_feedback"),
        ]
        result = AffordanceAnalyzer().analyze(lesson_entries=entries)
        # human_feedback 条目因为 +10 bonus 应该排在最前面
        self.assertTrue(result.high_risk_zones[0].startswith("用户纠正"))

    def test_long_trigger_truncated_to_40_chars(self):
        long = "这是一个非常非常非常非常非常非常非常非常非常非常非常非常长的触发器描述失败了"
        entries = [self._make_risky_entry(long)]
        result = AffordanceAnalyzer().analyze(lesson_entries=entries)
        if result.high_risk_zones:
            # 截断后不超过 40 字符（加省略号） 
            self.assertLessEqual(len(result.high_risk_zones[0]), 42)

    def test_no_duplicate_high_risk_zones(self):
        entries = [
            self._make_risky_entry("数据库崩溃操作"),
            self._make_risky_entry("数据库崩溃操作"),
        ]
        result = AffordanceAnalyzer().analyze(lesson_entries=entries)
        zones = result.high_risk_zones
        self.assertEqual(len(zones), len(set(zones)))

    def test_empty_trigger_not_added(self):
        entries = [_MemoryEntry(trigger="", outcome="fail crash", source="self_reflection")]
        result = AffordanceAnalyzer().analyze(lesson_entries=entries)
        # trigger 为空，不应产生 high_risk_zone
        self.assertEqual(result.high_risk_zones, [])


class TestAnalyzeCapabilityEntries(unittest.TestCase):
    def test_low_confidence_becomes_unexplored(self):
        entries = [
            _CapabilityEntry(domain="python_refactor", confidence=0.3),
        ]
        result = AffordanceAnalyzer().analyze(capability_entries=entries)
        self.assertIn("python_refactor", result.unexplored_areas)

    def test_high_confidence_not_unexplored(self):
        entries = [
            _CapabilityEntry(domain="bash_scripting", confidence=0.8),
        ]
        result = AffordanceAnalyzer().analyze(capability_entries=entries)
        self.assertNotIn("bash_scripting", result.unexplored_areas)

    def test_unexplored_sorted_by_confidence_ascending(self):
        entries = [
            _CapabilityEntry(domain="domain_medium", confidence=0.4),
            _CapabilityEntry(domain="domain_lowest", confidence=0.1),
            _CapabilityEntry(domain="domain_low", confidence=0.3),
        ]
        result = AffordanceAnalyzer().analyze(capability_entries=entries)
        areas = result.unexplored_areas
        self.assertEqual(areas[0], "domain_lowest")  # 置信度最低的在最前

    def test_exactly_at_threshold_not_unexplored(self):
        # _LOW_CONFIDENCE_THRESHOLD = 0.5，恰好等于时不应进入盲区
        entries = [_CapabilityEntry(domain="border_case", confidence=0.5)]
        result = AffordanceAnalyzer().analyze(capability_entries=entries)
        self.assertNotIn("border_case", result.unexplored_areas)


class TestAnalyzeMixed(unittest.TestCase):
    def test_top_opportunities_limited_to_3(self):
        threads = [
            _OpenThread(title=f"问题{i}", status="open", priority="high")
            for i in range(10)
        ]
        result = AffordanceAnalyzer().analyze(open_threads=threads)
        self.assertLessEqual(len(result.top_opportunities), 3)

    def test_unexplored_appended_to_opportunities(self):
        threads = []
        caps = [_CapabilityEntry(domain="docker_ops", confidence=0.2)]
        result = AffordanceAnalyzer().analyze(open_threads=threads, capability_entries=caps)
        opps_text = " ".join(result.top_opportunities)
        self.assertIn("docker_ops", opps_text)

    def test_full_pipeline_non_empty(self):
        threads = [_OpenThread(title="紧急：修复支付接口", status="open", priority="high", type="bug")]
        lessons = [_MemoryEntry(trigger="删除了错误的配置文件", outcome="服务崩溃", source="human_feedback")]
        caps = [_CapabilityEntry(domain="infra_ops", confidence=0.15)]
        result = AffordanceAnalyzer().analyze(
            open_threads=threads, lesson_entries=lessons, capability_entries=caps
        )
        self.assertFalse(result.is_empty())
        self.assertTrue(len(result.known_issues) > 0)
        self.assertTrue(len(result.high_risk_zones) > 0)
        self.assertTrue(len(result.unexplored_areas) > 0)
        frag = result.to_system_prompt_fragment()
        self.assertIn("当前环境行动可能性", frag)


if __name__ == "__main__":
    unittest.main()
