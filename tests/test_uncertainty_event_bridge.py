"""
tests/test_uncertainty_event_bridge.py — 方案三：ProprioceptionModule 的
uncertainty 信号接入事件总线测试。

覆盖：
  1. Agent._maybe_publish_uncertainty_signal()：连续 N 轮超阈值才发布，
     中途掉回阈值以下时计数重置
  2. 发布后 streak 重置，不会同一段持续状态重复发多条
  3. _current_task_domain_hint() 复用 phase_g._infer_domain()
  4. SoftGoalDeriver._recent_uncertainty_domains() 事件读取与降级路径
  5. _from_unexplored_capabilities() 中两路证据（sparse + uncertainty）
     同时命中时 novelty 加权取较大值而非相乘
"""

from __future__ import annotations

import tempfile
import unittest
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from mini_agent.agent import Agent
from mini_agent.perception.proprioception import AgentInternalState
from mini_agent.storage.paths import AgentPaths


@dataclass
class _ProprioceptionCfg:
    uncertainty_threshold: float = 0.45
    uncertainty_streak_required: int = 3


@dataclass
class _Cfg:
    project_root: Path
    proprioception: _ProprioceptionCfg = field(default_factory=_ProprioceptionCfg)


def _make_bare_agent(tmp_path, history=None):
    agent = Agent.__new__(Agent)
    agent.cfg = _Cfg(project_root=Path(tmp_path))
    agent._uncertainty_streak = 0
    agent._session = None

    class _Hist:
        def __init__(self, hist):
            self._history = hist or []

    agent._hist = _Hist(history)
    return agent


class TestMaybePublishUncertaintySignal(unittest.TestCase):
    def test_requires_consecutive_streak(self):
        with tempfile.TemporaryDirectory() as td:
            agent = _make_bare_agent(td)
            from mini_agent.perception import system_events as se

            for _ in range(2):
                agent._maybe_publish_uncertainty_signal(AgentInternalState(uncertainty=0.5))
            events = se.poll_since(
                AgentPaths(Path(td)), consumer_name="test_reader",
                event_types=["proprioception.uncertainty_sustained"],
            )
            self.assertEqual(events, [])  # 只有 2 轮，未达到默认 streak_required=3

            agent._maybe_publish_uncertainty_signal(AgentInternalState(uncertainty=0.5))
            events = se.poll_since(
                AgentPaths(Path(td)), consumer_name="test_reader",
                event_types=["proprioception.uncertainty_sustained"],
            )
            self.assertEqual(len(events), 1)  # 第 3 轮达标，发布一次

    def test_streak_resets_when_below_threshold(self):
        with tempfile.TemporaryDirectory() as td:
            agent = _make_bare_agent(td)
            agent._maybe_publish_uncertainty_signal(AgentInternalState(uncertainty=0.5))
            agent._maybe_publish_uncertainty_signal(AgentInternalState(uncertainty=0.1))  # 掉回阈值以下
            self.assertEqual(agent._uncertainty_streak, 0)

    def test_no_duplicate_publish_within_same_sustained_period(self):
        with tempfile.TemporaryDirectory() as td:
            agent = _make_bare_agent(td)
            from mini_agent.perception import system_events as se

            for _ in range(5):  # 持续 5 轮都超阈值
                agent._maybe_publish_uncertainty_signal(AgentInternalState(uncertainty=0.5))
            events = se.poll_since(
                AgentPaths(Path(td)), consumer_name="test_reader2",
                event_types=["proprioception.uncertainty_sustained"],
            )
            # streak_required=3：第3轮发布一次并重置，第4/5轮重新累积到2，未再次发布
            self.assertEqual(len(events), 1)


class TestCurrentTaskDomainHint(unittest.TestCase):
    def test_infers_domain_from_last_user_message(self):
        with tempfile.TemporaryDirectory() as td:
            agent = _make_bare_agent(td, history=[
                {"role": "user", "content": "帮我写个单元测试 test case"},
            ])
            hint = agent._current_task_domain_hint()
            self.assertEqual(hint, "testing")

    def test_empty_history_returns_empty_string(self):
        with tempfile.TemporaryDirectory() as td:
            agent = _make_bare_agent(td, history=[])
            self.assertEqual(agent._current_task_domain_hint(), "")


class TestSoftGoalDeriverUncertaintyConsumption(unittest.TestCase):
    def test_recent_uncertainty_domains_reads_events(self):
        from mini_agent.evolution.soft_goal_deriver import SoftGoalDeriver
        from mini_agent.perception import system_events as se

        with tempfile.TemporaryDirectory() as td:
            paths = AgentPaths(Path(td))
            se.publish(
                paths, source="test", event_type="proprioception.uncertainty_sustained",
                tier="tick", payload={"recent_domain_hint": "testing"},
            )
            deriver = SoftGoalDeriver(paths, _Cfg(project_root=Path(td)))
            domains = deriver._recent_uncertainty_domains()
            self.assertEqual(domains, ["testing"])

    def test_recent_uncertainty_domains_degrades_on_exception(self):
        from mini_agent.evolution.soft_goal_deriver import SoftGoalDeriver

        with tempfile.TemporaryDirectory() as td:
            paths = AgentPaths(Path(td))
            deriver = SoftGoalDeriver(paths, _Cfg(project_root=Path(td)))
            # 没有事件文件时也应正常返回空列表，不抛异常
            self.assertEqual(deriver._recent_uncertainty_domains(), [])

    def test_two_signals_take_max_not_product(self):
        from mini_agent.evolution.soft_goal_deriver import SoftGoalDeriver

        with tempfile.TemporaryDirectory() as td:
            paths = AgentPaths(Path(td))
            deriver = SoftGoalDeriver(paths, _Cfg(project_root=Path(td)))

            @dataclass
            class _CapEntry:
                capability_name: str
                total_calls: int = 0

            import mini_agent.evolution.phase_g as phase_g
            orig_load = phase_g.load_capability_map
            phase_g.load_capability_map = lambda _paths: [_CapEntry("testing_utils", 0)]

            deriver._recently_explored_domains = lambda cooldown_days=None: set()
            deriver._recent_sparse_region_tokens = lambda: ["testing"]
            deriver._recent_uncertainty_domains = lambda: ["testing"]

            try:
                candidates = deriver._from_unexplored_capabilities()
            finally:
                phase_g.load_capability_map = orig_load

            self.assertEqual(len(candidates), 1)
            base_novelty = 1.0 / (1 + 0)
            expected_max_weighted = base_novelty * min(1.6, 1.0 + 0.2 * 1)
            self.assertAlmostEqual(candidates[0].novelty, expected_max_weighted, places=5)


if __name__ == "__main__":
    unittest.main()
