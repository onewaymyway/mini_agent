"""
tests/test_workflow_editor_routes.py

覆盖 next_doc/workflow_visual_editor_plan.md §5.5 里程碑 M2 新增的 REST 端点（api/routes.py）：
  - GET  /v1/workflow_editor/meta
  - GET  /v1/workflows/{name}/editor
  - POST /v1/workflows/{name}/editor/validate
  - PUT  /v1/workflows/{name}/editor

沿用 tests/test_goal_execution_spec_kanban_routes.py 的最小 FastAPI app 模式，不拉起完整
HttpServer；只验证路由层的参数透传、状态码与错误 detail 结构（保存/校验的内部逻辑由
tests/test_workflow_editor_helpers.py 覆盖）。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_workflow_editor_routes.py -q
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.routes import router

SRC = """\
# 注释要保留
name: demo
steps:
  - id: a
    name: A
    prompt: 第一步
  - id: b
    name: B
    prompt: "第二步 {a.output}"
    depends_on: [a]
"""


def _make_client(root: Path, **wf) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    wf_cfg = SimpleNamespace(
        visual_editor_enabled=wf.get("visual_editor_enabled", True),
        editor_backup_keep=20, script_step_enabled=False, python_step_enabled=False,
        git_hint_enabled=False, validate_role_refs_on_save=False,
    )
    cfg = SimpleNamespace(project_root=str(root), workflow=wf_cfg)
    app.state.http_server = SimpleNamespace(bridge=SimpleNamespace(agent=SimpleNamespace(cfg=cfg)))
    return TestClient(app)


class TestWorkflowEditorRoutes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        wdir = self.root / ".agent" / "workflows"
        wdir.mkdir(parents=True)
        self.path = wdir / "demo.yaml"
        self.path.write_text(SRC, encoding="utf-8")
        self.client = _make_client(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def _open(self) -> dict:
        r = self.client.get("/v1/workflows/demo/editor")
        self.assertEqual(r.status_code, 200)
        return r.json()

    # ── GET ────────────────────────────────────────────────────────────

    def test_get_editor_document(self):
        body = self._open()
        self.assertEqual(body["name"], "demo")
        self.assertEqual(body["mode"], "file")
        self.assertEqual([s["id"] for s in body["draft"]["steps"]], ["a", "b"])
        self.assertEqual(len(body["base_hash"]), 64)
        self.assertTrue(body["editor_enabled"])

    def test_get_editor_404(self):
        r = self.client.get("/v1/workflows/nope/editor")
        self.assertEqual(r.status_code, 404)
        self.assertEqual(r.json()["detail"]["code"], "not_found")

    def test_meta_endpoint(self):
        r = self.client.get("/v1/workflow_editor/meta")
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("demo", body["workflows"])
        self.assertTrue(any(t["name"] == "agent" for t in body["step_types"]))
        self.assertTrue(body["switches"]["visual_editor_enabled"])

    # ── validate ───────────────────────────────────────────────────────

    def test_validate_ok_and_error_shapes(self):
        doc = self._open()
        r = self.client.post("/v1/workflows/demo/editor/validate", json={"draft": doc["draft"]})
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        self.assertEqual(r.json()["batches"], [["a"], ["b"]])

        bad = doc["draft"]
        bad["steps"][1]["depends_on"] = ["ghost"]
        r2 = self.client.post("/v1/workflows/demo/editor/validate", json={"draft": bad})
        self.assertEqual(r2.status_code, 200)             # 校验端点本身成功，结果里 ok=false
        self.assertFalse(r2.json()["ok"])
        self.assertIn("b", r2.json()["errors_by_step"])

    def test_validate_rejects_non_object_body(self):
        r = self.client.post("/v1/workflows/demo/editor/validate", json=[1, 2])
        self.assertEqual(r.status_code, 400)

    def test_validate_missing_draft_is_400(self):
        r = self.client.post("/v1/workflows/demo/editor/validate", json={})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["detail"]["code"], "bad_request")

    # ── PUT ────────────────────────────────────────────────────────────

    def test_put_saves_and_keeps_comment(self):
        doc = self._open()
        doc["draft"]["steps"][0]["prompt"] = "改过的第一步"
        r = self.client.put("/v1/workflows/demo/editor", json={
            "draft": doc["draft"], "base_hash": doc["base_hash"], "prompt_files": doc["prompt_files"],
        })
        self.assertEqual(r.status_code, 200, r.text)
        out = r.json()
        self.assertEqual(out["status"], "saved")
        self.assertEqual(out["changed_steps"]["modified"], ["a"])
        self.assertIsNotNone(out["backup_id"])
        text = self.path.read_text(encoding="utf-8")
        self.assertIn("# 注释要保留", text)
        self.assertIn("改过的第一步", text)
        # 用返回的新 hash 可以继续保存（无修改 → unchanged）
        r2 = self.client.put("/v1/workflows/demo/editor", json={
            "draft": doc["draft"], "base_hash": out["base_hash"],
        })
        self.assertEqual(r2.status_code, 200)
        self.assertEqual(r2.json()["status"], "unchanged")

    def test_put_conflict_is_409_with_current_hash(self):
        doc = self._open()
        self.path.write_text(SRC + "# 别处改过\n", encoding="utf-8")
        doc["draft"]["description"] = "x"
        r = self.client.put("/v1/workflows/demo/editor", json={
            "draft": doc["draft"], "base_hash": doc["base_hash"],
        })
        self.assertEqual(r.status_code, 409)
        detail = r.json()["detail"]
        self.assertEqual(detail["code"], "conflict")
        self.assertEqual(len(detail["current_hash"]), 64)
        # 强制覆盖
        r2 = self.client.put("/v1/workflows/demo/editor", json={
            "draft": doc["draft"], "base_hash": doc["base_hash"], "force": True,
        })
        self.assertEqual(r2.status_code, 200)

    def test_put_validation_failure_is_422_with_errors_by_step(self):
        doc = self._open()
        before = self.path.read_bytes()
        doc["draft"]["steps"][0]["depends_on"] = ["b"]      # a → b → a 成环
        r = self.client.put("/v1/workflows/demo/editor", json={
            "draft": doc["draft"], "base_hash": doc["base_hash"],
        })
        self.assertEqual(r.status_code, 422)
        detail = r.json()["detail"]
        self.assertEqual(detail["code"], "validation_failed")
        self.assertIn("a", detail["errors_by_step"])
        self.assertIn("b", detail["errors_by_step"])
        self.assertTrue(detail["cycle"])
        self.assertEqual(self.path.read_bytes(), before)

    def test_put_forbidden_when_editor_disabled_but_get_still_works(self):
        client = _make_client(self.root, visual_editor_enabled=False)
        r = client.get("/v1/workflows/demo/editor")
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["editor_enabled"])
        r2 = client.put("/v1/workflows/demo/editor", json={
            "draft": r.json()["draft"], "base_hash": r.json()["base_hash"],
        })
        self.assertEqual(r2.status_code, 403)
        self.assertEqual(r2.json()["detail"]["code"], "editor_disabled")
        self.assertEqual(self.path.read_text(encoding="utf-8"), SRC)

    def test_existing_workflow_yaml_endpoint_unaffected(self):
        r = self.client.get("/v1/workflows/demo")
        self.assertEqual(r.status_code, 200)
        self.assertIn("# 注释要保留", r.json()["yaml"])


if __name__ == "__main__":
    unittest.main()
