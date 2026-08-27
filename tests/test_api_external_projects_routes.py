"""tests/test_api_external_projects_routes.py — 外部项目管理接入看板
（external_projects_kanban_integration_plan.md 阶段1）新增 HTTP 路由测试。

风格对齐 tests/test_hybrid_exec_summary_route.py：不拉起完整 HttpServer，
只挂 router 到一个最小 FastAPI app；`ExternalProjectRegistry` 存储路径
指到临时目录，不污染真实的 ~/.mini_agent/external_projects.json。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.routes import router
from mini_agent.external_projects.registry import ExternalProjectRegistry

VALID_MANIFEST = """
name: demo_project
entrypoints:
  scan:
    cmd: "python -c \\"print(1)\\""
    timeout_sec: 30
  analyze:
    cmd: "python -c \\"import sys; print(sys.argv[1])\\""
    params:
      - name: code
        required: true
        help: "股票代码"
review:
  cadence: weekly
  enabled: true
"""


def _make_app() -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.http_server = None  # 这批端点不依赖 http_server，走单用户放行
    return TestClient(app)


class TestExternalProjectsKanbanRoutes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp_path = Path(self._tmp.name)
        self.project_dir = self.tmp_path / "demo_project"
        self.project_dir.mkdir()
        (self.project_dir / "project.yaml").write_text(VALID_MANIFEST, encoding="utf-8")

        self.registry_path = self.tmp_path / "registry.json"
        self._patch_default_registry_path()
        self.client = _make_app()

    def tearDown(self):
        self._unpatch_default_registry_path()
        self._tmp.cleanup()

    def _patch_default_registry_path(self):
        import mini_agent.external_projects.registry as registry_mod

        self._orig_default_path = registry_mod.DEFAULT_REGISTRY_PATH
        registry_mod.DEFAULT_REGISTRY_PATH = self.registry_path

    def _unpatch_default_registry_path(self):
        import mini_agent.external_projects.registry as registry_mod

        registry_mod.DEFAULT_REGISTRY_PATH = self._orig_default_path

    def _register_project(self):
        registry = ExternalProjectRegistry(self.registry_path)
        registry.register("demo_project", self.project_dir)

    # ── register ─────────────────────────────────────────────────────

    def test_register_happy_path(self):
        resp = self.client.post(
            "/v1/external_projects/register",
            json={"path": str(self.project_dir)},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["project"]["name"], "demo_project")

    def test_register_missing_path_returns_400(self):
        resp = self.client.post("/v1/external_projects/register", json={})
        self.assertEqual(resp.status_code, 400)

    def test_register_duplicate_returns_400(self):
        self._register_project()
        resp = self.client.post(
            "/v1/external_projects/register",
            json={"path": str(self.project_dir), "name": "demo_project"},
        )
        self.assertEqual(resp.status_code, 400)

    def test_status_route_includes_entrypoints_for_buttons(self):
        # [external_projects_kanban_integration_plan.md 阶段5] 看板改成
        # 直接列 entrypoints 按钮而不是让用户手填 key，前提是
        # GET /v1/self/external_projects 里带上了每个项目的 entrypoints。
        self._register_project()
        resp = self.client.get("/v1/self/external_projects")
        self.assertEqual(resp.status_code, 200)
        projects = {p["name"]: p for p in resp.json()["projects"]}
        keys = {ep["key"] for ep in projects["demo_project"]["entrypoints"]}
        self.assertEqual(keys, {"scan", "analyze"})

    def test_register_invalid_manifest_returns_400(self):
        bad_dir = self.tmp_path / "bad_project"
        bad_dir.mkdir()
        (bad_dir / "project.yaml").write_text("not: [valid", encoding="utf-8")
        resp = self.client.post(
            "/v1/external_projects/register",
            json={"path": str(bad_dir)},
        )
        self.assertEqual(resp.status_code, 400)

    # ── trigger_run ──────────────────────────────────────────────────

    def test_trigger_run_unregistered_project_returns_404(self):
        resp = self.client.post(
            "/v1/external_projects/not_registered/trigger_run",
            json={"entrypoint": "scan"},
        )
        self.assertEqual(resp.status_code, 404)

    def test_trigger_run_missing_entrypoint_returns_400(self):
        self._register_project()
        resp = self.client.post("/v1/external_projects/demo_project/trigger_run", json={})
        self.assertEqual(resp.status_code, 400)

    def test_trigger_run_happy_path(self):
        self._register_project()
        resp = self.client.post(
            "/v1/external_projects/demo_project/trigger_run",
            json={"entrypoint": "scan"},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["project_name"], "demo_project")
        self.assertEqual(data["entrypoint_key"], "scan")
        self.assertEqual(data["returncode"], 0)
        self.assertEqual(data["trigger"], "manual")

    # ── trigger_run 传参（external_projects_kanban_integration_plan.md 阶段6）──

    def test_status_route_includes_entrypoint_params_schema(self):
        self._register_project()
        resp = self.client.get("/v1/self/external_projects")
        self.assertEqual(resp.status_code, 200)
        projects = {p["name"]: p for p in resp.json()["projects"]}
        entrypoints = {ep["key"]: ep for ep in projects["demo_project"]["entrypoints"]}
        params = {p["name"]: p for p in entrypoints["analyze"]["params"]}
        self.assertEqual(params["code"]["required"], True)
        self.assertEqual(entrypoints["scan"]["params"], [])

    def test_trigger_run_with_params_happy_path(self):
        self._register_project()
        resp = self.client.post(
            "/v1/external_projects/demo_project/trigger_run",
            json={"entrypoint": "analyze", "params": {"code": "600519"}},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["returncode"], 0)

    def test_trigger_run_missing_required_param_returns_400(self):
        self._register_project()
        resp = self.client.post(
            "/v1/external_projects/demo_project/trigger_run",
            json={"entrypoint": "analyze", "params": {}},
        )
        self.assertEqual(resp.status_code, 400)

    def test_trigger_run_unknown_param_returns_400(self):
        self._register_project()
        resp = self.client.post(
            "/v1/external_projects/demo_project/trigger_run",
            json={"entrypoint": "analyze", "params": {"code": "600519", "bogus": "x"}},
        )
        self.assertEqual(resp.status_code, 400)

    def test_trigger_run_params_not_object_returns_400(self):
        self._register_project()
        resp = self.client.post(
            "/v1/external_projects/demo_project/trigger_run",
            json={"entrypoint": "analyze", "params": "not-an-object"},
        )
        self.assertEqual(resp.status_code, 400)

    # ── ledger ───────────────────────────────────────────────────────

    def test_ledger_empty_when_never_run(self):
        self._register_project()
        resp = self.client.get("/v1/external_projects/demo_project/ledger")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"records": []})

    def test_ledger_unregistered_project_returns_404(self):
        resp = self.client.get("/v1/external_projects/not_registered/ledger")
        self.assertEqual(resp.status_code, 404)

    def test_ledger_reflects_trigger_run(self):
        self._register_project()
        self.client.post(
            "/v1/external_projects/demo_project/trigger_run",
            json={"entrypoint": "scan"},
        )
        resp = self.client.get("/v1/external_projects/demo_project/ledger")
        data = resp.json()
        self.assertEqual(len(data["records"]), 1)
        self.assertEqual(data["records"][0]["entrypoint"], "scan")
        self.assertEqual(data["records"][0]["trigger"], "manual")

    # ── backlog ──────────────────────────────────────────────────────

    def test_backlog_get_empty(self):
        self._register_project()
        resp = self.client.get("/v1/external_projects/demo_project/backlog")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"items": []})

    def test_backlog_post_missing_summary_returns_400(self):
        self._register_project()
        resp = self.client.post("/v1/external_projects/demo_project/backlog", json={})
        self.assertEqual(resp.status_code, 400)

    def test_backlog_post_forces_user_feedback_source(self):
        self._register_project()
        resp = self.client.post(
            "/v1/external_projects/demo_project/backlog",
            json={"summary": "看板手填的一条反馈", "source": "outcome_review"},
        )
        self.assertEqual(resp.status_code, 200)
        item = resp.json()["item"]
        self.assertEqual(item["source"], "user_feedback")
        self.assertEqual(item["status"], "open")

        list_resp = self.client.get("/v1/external_projects/demo_project/backlog")
        self.assertEqual(len(list_resp.json()["items"]), 1)

        filtered = self.client.get(
            "/v1/external_projects/demo_project/backlog", params={"status": "open"}
        )
        self.assertEqual(len(filtered.json()["items"]), 1)
        filtered_landed = self.client.get(
            "/v1/external_projects/demo_project/backlog", params={"status": "landed"}
        )
        self.assertEqual(filtered_landed.json()["items"], [])

    # ── review ───────────────────────────────────────────────────────

    def test_review_preview_returns_template_and_enabled_flag(self):
        self._register_project()
        resp = self.client.get("/v1/external_projects/demo_project/review")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertIn("demo_project", data["template"])
        self.assertTrue(data["enabled"])

    def test_review_unregistered_project_returns_404(self):
        resp = self.client.get("/v1/external_projects/not_registered/review")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
