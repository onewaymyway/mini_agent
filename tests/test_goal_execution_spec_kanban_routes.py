"""
tests/test_goal_execution_spec_kanban_routes.py

覆盖 next_doc/goal_execution_spec_generation_plan.md §6.1/§6.3/§6.4 看板 UI
落地时新增的 REST 端点（api/routes.py）：
  - GET  /v1/goal_execution_spec_templates
  - GET  /v1/goals/{goal_id}/execution_spec
  - POST /v1/goals/{goal_id}/execution_spec/generate
  - POST /v1/goals/{goal_id}/execution_spec/revise
  - POST /v1/goals/{goal_id}/execution_spec/confirm
  - POST /v1/goals/{goal_id}/execution_spec/close_check

沿用 tests/test_kanban_config_routes.py 的最小 FastAPI app 模式，不拉起完整
HttpServer；`GoalExecutionSpecBuilder`/`GoalBacklog.maybe_close_goal_by_
overall_criteria` 均打桩，只验证路由层的参数透传、状态码、错误处理，不重复
tests/test_goal_execution_spec.py 已经覆盖的生成器内部逻辑。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_goal_execution_spec_kanban_routes.py -q
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.routes import router
from mini_agent.perception.goal_backlog import load_goal_backlog
from mini_agent.perception.goal_execution_spec import GoalExecutionSpec, save_spec, load_spec
from mini_agent.storage.paths import AgentPaths


def _make_client(root: Path) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    cfg = SimpleNamespace(project_root=str(root))
    bridge = SimpleNamespace(agent=SimpleNamespace(cfg=cfg))
    app.state.http_server = SimpleNamespace(bridge=bridge)
    return TestClient(app)


class TestGoalExecutionSpecKanbanRoutes(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.paths = AgentPaths(self.root)
        self.client = _make_client(self.root)
        backlog = load_goal_backlog(self.paths)
        self.goal = backlog.add_goal(title="周报生成", description="每周整理一次数据报告", priority=50)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_templates_endpoint_returns_list(self):
        resp = self.client.get("/v1/goal_execution_spec_templates")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        templates = body["templates"]
        self.assertTrue(any(t["id"] == "periodic_report" for t in templates))
        self.assertIsNone(body["suggested_template_id"])

    def test_templates_endpoint_suggests_by_goal_title(self):
        resp = self.client.get(
            "/v1/goal_execution_spec_templates",
            params={"goal_title": "每周数据周报", "goal_description": "汇总核心指标"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["suggested_template_id"], "periodic_report")

    def test_get_execution_spec_none_when_not_generated(self):
        resp = self.client.get(f"/v1/goals/{self.goal.id}/execution_spec")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["spec"])

    def test_generate_goal_not_found(self):
        resp = self.client.post("/v1/goals/does_not_exist/execution_spec/generate", json={})
        self.assertEqual(resp.status_code, 404)

    def test_generate_builds_and_saves_draft(self):
        fake_spec = GoalExecutionSpec(version=1, goal_id=self.goal.id)
        fake_spec.deliverables = []
        with patch(
            "mini_agent.perception.goal_execution_spec.GoalExecutionSpecBuilder.build_draft",
            return_value=fake_spec,
        ):
            resp = self.client.post(
                f"/v1/goals/{self.goal.id}/execution_spec/generate",
                json={"template_id": "periodic_report"},
            )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()["spec"]
        self.assertEqual(data["goal_id"], self.goal.id)
        self.assertFalse(data["confirmed"])
        # 落盘也应该能读到同一份草稿
        on_disk = load_spec(self.paths, self.goal.id)
        self.assertIsNotNone(on_disk)
        self.assertEqual(on_disk.version, 1)

    def test_generate_forwards_mode_override_and_returns_effective_path(self):
        """[goal_execution_spec_generation_plan.md §3 输入源 1 /
        implementation_record.md §7.5/§9 未实施清单第 2 条] REST 层新增的
        单次 mode 覆盖：body 里的 "mode" 要透传进 GoalExecutionSpecBuilder
        构造函数，响应体要带上 builder.last_effective_path。"""
        fake_spec = GoalExecutionSpec(version=1, goal_id=self.goal.id)
        captured = {}

        class _FakeBuilder:
            def __init__(self, cfg, mode=None):
                captured["mode"] = mode
                self.last_effective_path = "agent"

            def build_draft(self, *a, **kw):
                return fake_spec

        with patch("mini_agent.perception.goal_execution_spec.GoalExecutionSpecBuilder", _FakeBuilder):
            resp = self.client.post(
                f"/v1/goals/{self.goal.id}/execution_spec/generate",
                json={"mode": "agent"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(captured["mode"], "agent")
        self.assertEqual(resp.json()["effective_path"], "agent")

    def test_generate_without_mode_passes_none(self):
        """不传 mode 时透传 None（服务端回退配置文件默认值），而不是空
        字符串——`GoalExecutionSpecBuilder.__init__` 用 `mode or ...` 的
        写法，传空字符串同样会回退，但显式测 None 更贴近 body.get() 的
        真实返回值，避免以后改成别的写法时悄悄改变语义。"""
        fake_spec = GoalExecutionSpec(version=1, goal_id=self.goal.id)
        captured = {}

        class _FakeBuilder:
            def __init__(self, cfg, mode=None):
                captured["mode"] = mode
                self.last_effective_path = "llm"

            def build_draft(self, *a, **kw):
                return fake_spec

        with patch("mini_agent.perception.goal_execution_spec.GoalExecutionSpecBuilder", _FakeBuilder):
            resp = self.client.post(f"/v1/goals/{self.goal.id}/execution_spec/generate", json={})
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(captured["mode"])
        self.assertEqual(resp.json()["effective_path"], "llm")

    def test_revise_requires_prior_draft(self):
        resp = self.client.post(
            f"/v1/goals/{self.goal.id}/execution_spec/revise",
            json={"feedback": "报告要更详细"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_revise_requires_nonempty_feedback(self):
        save_spec(self.paths, self.goal.id, GoalExecutionSpec(version=1, goal_id=self.goal.id))
        resp = self.client.post(
            f"/v1/goals/{self.goal.id}/execution_spec/revise", json={"feedback": "  "},
        )
        self.assertEqual(resp.status_code, 400)

    def test_revise_calls_builder_with_locked_fields(self):
        prior = GoalExecutionSpec(version=1, goal_id=self.goal.id)
        save_spec(self.paths, self.goal.id, prior)
        revised = GoalExecutionSpec(version=2, goal_id=self.goal.id)
        with patch(
            "mini_agent.perception.goal_execution_spec.GoalExecutionSpecBuilder.revise",
            return_value=revised,
        ) as mock_revise:
            resp = self.client.post(
                f"/v1/goals/{self.goal.id}/execution_spec/revise",
                json={"feedback": "报告要更详细", "locked_fields": ["deliverables"]},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["spec"]["version"], 2)
        args, kwargs = mock_revise.call_args
        self.assertEqual(kwargs.get("locked_fields"), ["deliverables"])

    def test_revise_forwards_mode_override(self):
        prior = GoalExecutionSpec(version=1, goal_id=self.goal.id)
        save_spec(self.paths, self.goal.id, prior)
        revised = GoalExecutionSpec(version=2, goal_id=self.goal.id)
        captured = {}

        class _FakeBuilder:
            def __init__(self, cfg, mode=None):
                captured["mode"] = mode
                self.last_effective_path = "llm"

            def revise(self, *a, **kw):
                return revised

        with patch("mini_agent.perception.goal_execution_spec.GoalExecutionSpecBuilder", _FakeBuilder):
            resp = self.client.post(
                f"/v1/goals/{self.goal.id}/execution_spec/revise",
                json={"feedback": "更细一点", "mode": "llm"},
            )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(captured["mode"], "llm")
        self.assertEqual(resp.json()["effective_path"], "llm")

    def test_confirm_requires_prior_draft(self):
        resp = self.client.post(f"/v1/goals/{self.goal.id}/execution_spec/confirm")
        self.assertEqual(resp.status_code, 404)

    def test_confirm_marks_confirmed_and_flips_goal_pointer(self):
        save_spec(self.paths, self.goal.id, GoalExecutionSpec(version=1, goal_id=self.goal.id))
        resp = self.client.post(f"/v1/goals/{self.goal.id}/execution_spec/confirm")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["spec"]["confirmed"])
        self.assertTrue(body["goal"]["execution_spec_confirmed"])
        on_disk = load_spec(self.paths, self.goal.id)
        self.assertTrue(on_disk.confirmed)

    def test_close_check_not_active_short_circuits(self):
        backlog = load_goal_backlog(self.paths)
        backlog.set_status(self.goal.id, "completed")
        resp = self.client.post(f"/v1/goals/{self.goal.id}/execution_spec/close_check")
        self.assertEqual(resp.status_code, 200)
        self.assertIsNone(resp.json()["outcome"])

    def test_close_check_delegates_to_backlog_method(self):
        with patch(
            "mini_agent.perception.goal_backlog.GoalBacklog.maybe_close_goal_by_overall_criteria",
            return_value="closed",
        ):
            resp = self.client.post(f"/v1/goals/{self.goal.id}/execution_spec/close_check")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["outcome"], "closed")


if __name__ == "__main__":
    unittest.main()
