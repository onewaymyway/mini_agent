"""
tests/test_cycle_diagnostics_tuning_routes.py

覆盖 next_doc/goal_cron_cycle_diagnostics_and_interactive_tuning_plan.md
Stage 1/2/3 在 REST 层（`api/routes.py`）的端到端行为。之前这几个端点只有
`perception/cycle_diagnostics.py` / `perception/cycle_tuning.py` 模块级的
单元测试（`test_cycle_diagnostics.py` / `test_cycle_tuning.py`），没有真正
经过 FastAPI 路由 + Request 解析这一层——本文件补上，同时也是
`apps/mini_agent_kanban` 新增的诊断/调优控件（`client.py` 里
`get_cycle_diagnostics` / `list_tuning_proposals` / `create_tuning_proposal`
/ `confirm_tuning_proposal` / `apply_tuning_proposal` / `reject_tuning_proposal`
/ `suggest_tuning_proposal`）所依赖的响应结构的回归保护：看板代码假定的
字段路径（如 `diagnostics.recent_health_alerts`、
`proposal.proposed_changes[].{param,from,to,reason}`）如果被后端改动，这里
会先失败。

沿用 `test_kanban_config_routes.py` 的最小 FastAPI app 模式（不拉起完整
HttpServer），`_require_owner` 在单用户模式（不设置 `user_ctx`）下直接放行。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.routes import router
from mini_agent.config.loader import load_config
from mini_agent.evolution.cron_scheduler import CronScheduler
from mini_agent.evolution.goal_cron_bridge import make_goal_recurring
from mini_agent.perception.goal_backlog import load_goal_backlog
from mini_agent.storage.paths import AgentPaths


def _make_app_client(cfg) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    bridge = SimpleNamespace(agent=SimpleNamespace(cfg=cfg, llm_helper=None))
    app.state.http_server = SimpleNamespace(bridge=bridge)
    return TestClient(app)


class _RouteTestBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        (self.root / "agent_config.json").write_text("{}", encoding="utf-8")
        self.cfg = load_config(config_file=self.root / "agent_config.json", project_root=self.root)
        self.paths = AgentPaths(self.root)
        self.backlog = load_goal_backlog(self.paths)
        self.client = _make_app_client(self.cfg)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _add_recurring_goal(self, title="Test Goal") -> str:
        node = self.backlog.add_goal(title, "desc", priority=10)
        self.backlog.save()
        cs = CronScheduler(self.paths)
        make_goal_recurring(self.backlog, cs, node.id, "interval:3600", "do the thing")
        return node.id


class TestCycleDiagnosticsRoute(_RouteTestBase):
    def test_404_for_unknown_goal(self):
        resp = self.client.get("/v1/goals/does_not_exist/cycle_diagnostics")
        self.assertEqual(resp.status_code, 404)

    def test_returns_structured_report_shape_kanban_depends_on(self):
        goal_id = self._add_recurring_goal()
        resp = self.client.get(f"/v1/goals/{goal_id}/cycle_diagnostics")
        self.assertEqual(resp.status_code, 200)
        diag = resp.json()["diagnostics"]
        # 看板 _render_goal_cycle_diagnostics_widget() 读取的字段路径：
        for key in (
            "found", "cycle_count", "execution_phase_mode", "execution_phase_locked",
            "status", "recent_health_alerts", "cron_health", "recent_cycle_summaries",
            "mechanism_notes", "llm_summary",
        ):
            self.assertIn(key, diag)
        self.assertTrue(diag["found"])
        self.assertIsNone(diag["llm_summary"])  # 默认未开启 Stage 3，不应该有值

    def test_summarize_query_param_without_llm_config_stays_none(self):
        # cycle_tuning.diagnostics_llm_summary_enabled 默认为 False，即使
        # 加了 ?summarize=true 也应该静默保持 None，不报错。
        goal_id = self._add_recurring_goal()
        resp = self.client.get(f"/v1/goals/{goal_id}/cycle_diagnostics?summarize=true")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["diagnostics"]["llm_summary"])


class TestTuningProposalRoutes(_RouteTestBase):
    def test_create_list_confirm_apply_roundtrip(self):
        goal_id = self._add_recurring_goal()

        # 创建（结构化 changes）
        resp = self.client.post(
            f"/v1/goals/{goal_id}/tuning_proposals",
            json={"changes": [{"param": "priority", "to": 42, "reason": "bump"}]},
        )
        self.assertEqual(resp.status_code, 200)
        proposal = resp.json()["proposal"]
        self.assertEqual(proposal["status"], "draft")
        change = proposal["proposed_changes"][0]
        # 看板卡片渲染依赖的字段：param/from/to/reason 都要在。
        for key in ("param", "from", "to", "reason"):
            self.assertIn(key, change)
        proposal_id = proposal["id"]

        # 列表能看到刚创建的草案
        resp = self.client.get(f"/v1/goals/{goal_id}/tuning_proposals")
        self.assertEqual(resp.status_code, 200)
        listed = resp.json()["proposals"]
        self.assertEqual(len(listed), 1)
        self.assertEqual(listed[0]["id"], proposal_id)

        # 确认
        resp = self.client.post(f"/v1/goals/{goal_id}/tuning_proposals/{proposal_id}/confirm")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["proposal"]["status"], "confirmed")

        # 应用
        resp = self.client.post(f"/v1/goals/{goal_id}/tuning_proposals/{proposal_id}/apply")
        self.assertEqual(resp.status_code, 200)
        applied = resp.json()["proposal"]
        self.assertEqual(applied["status"], "applied")
        self.assertIn("apply_results", applied)

        node = load_goal_backlog(self.paths).get(goal_id)
        self.assertEqual(node.priority, 42)

    def test_non_whitelisted_param_returns_400(self):
        goal_id = self._add_recurring_goal()
        resp = self.client.post(
            f"/v1/goals/{goal_id}/tuning_proposals",
            json={"changes": [{"param": "title", "to": "hacked"}]},
        )
        self.assertEqual(resp.status_code, 400)

    def test_reject_draft_proposal(self):
        goal_id = self._add_recurring_goal()
        resp = self.client.post(
            f"/v1/goals/{goal_id}/tuning_proposals",
            json={"changes": [{"param": "priority", "to": 5}]},
        )
        proposal_id = resp.json()["proposal"]["id"]
        resp = self.client.post(
            f"/v1/goals/{goal_id}/tuning_proposals/{proposal_id}/reject", json={"reason": "no"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["proposal"]["status"], "rejected")

    def test_apply_before_confirm_returns_400(self):
        goal_id = self._add_recurring_goal()
        resp = self.client.post(
            f"/v1/goals/{goal_id}/tuning_proposals",
            json={"changes": [{"param": "priority", "to": 5}]},
        )
        proposal_id = resp.json()["proposal"]["id"]
        resp = self.client.post(f"/v1/goals/{goal_id}/tuning_proposals/{proposal_id}/apply")
        self.assertEqual(resp.status_code, 400)

    def test_nl_text_without_llm_config_returns_400(self):
        # cycle_tuning.tuning_llm_parse_enabled 默认为 False。
        goal_id = self._add_recurring_goal()
        resp = self.client.post(
            f"/v1/goals/{goal_id}/tuning_proposals", json={"nl_text": "跑快一点"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_neither_changes_nor_nl_text_returns_400(self):
        goal_id = self._add_recurring_goal()
        resp = self.client.post(f"/v1/goals/{goal_id}/tuning_proposals", json={})
        self.assertEqual(resp.status_code, 400)

    def test_suggest_returns_null_when_nothing_to_suggest(self):
        goal_id = self._add_recurring_goal()
        resp = self.client.post(f"/v1/goals/{goal_id}/tuning_proposals/suggest")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["proposal"])  # 全新 Goal，没有任何规则命中


if __name__ == "__main__":
    unittest.main()
