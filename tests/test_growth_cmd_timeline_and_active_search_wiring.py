"""tests/test_growth_cmd_timeline_and_active_search_wiring.py — 覆盖
`cli/commands/growth_cmd.py` 新增的 `/growth timeline` 子命令，以及
`_get_web_search_fn()` 对 `web_search` 工具的绑定/兜底行为。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from mini_agent.cli.commands import growth_cmd
from mini_agent.evolution import growth_advisor as ga
from mini_agent.storage.paths import AgentPaths


def _make_paths(tmp: str) -> AgentPaths:
    return AgentPaths(project_root=Path(tmp))


class _FakeAgent:
    def __init__(self, paths, cfg=None):
        self._paths = paths
        self.cfg = cfg or object()


class TestGetWebSearchFn(unittest.TestCase):
    def test_returns_none_without_agent(self):
        self.assertIsNone(growth_cmd._get_web_search_fn(None))

    def test_returns_none_without_cfg(self):
        class A:
            cfg = None
        self.assertIsNone(growth_cmd._get_web_search_fn(A()))

    def test_returns_callable_with_agent_cfg(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            agent = _FakeAgent(paths)
            fn = growth_cmd._get_web_search_fn(agent)
            self.assertTrue(callable(fn))


class TestTimelineCommand(unittest.TestCase):
    def test_timeline_reports_unknown_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            agent = _FakeAgent(paths)
            # 不应该抛异常
            growth_cmd.handle_growth_cmd(["timeline", "does-not-exist"], agent=agent)

    def test_timeline_prints_events_for_known_candidate(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            agent = _FakeAgent(paths)
            backlog = ga.GrowthBacklog(paths)
            candidate = ga.GrowthCandidate(
                candidate_id="c1", title="数据分析", rationale="因为...",
                evidence_count=5, confidence=0.6,
            )
            backlog.save_all([candidate])
            ga.generate_growth_report(paths, candidate, cfg=None)
            # 不应该抛异常
            growth_cmd.handle_growth_cmd(["timeline", "c1"], agent=agent)

    def test_timeline_missing_arg_reports_usage(self):
        with tempfile.TemporaryDirectory() as tmp:
            paths = _make_paths(tmp)
            agent = _FakeAgent(paths)
            growth_cmd.handle_growth_cmd(["timeline"], agent=agent)


if __name__ == "__main__":
    unittest.main()
