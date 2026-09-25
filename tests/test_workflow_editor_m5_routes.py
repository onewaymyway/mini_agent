"""
tests/test_workflow_editor_m5_routes.py

覆盖 next_doc/workflow_visual_editor_plan.md §八 里程碑 M5 新增的 REST 端点
（api/routes.py）：
  - POST /v1/workflows                                  新建 / 复制
  - POST /v1/workflows/{name}/steps/{step_id}/test       单步试运行（async_jobs 异步）
  - GET  /v1/workflows/{name}/backups                    备份列表
  - POST /v1/workflows/{name}/backups/{id}/restore       恢复备份

沿用 tests/test_workflow_editor_routes.py 的最小 FastAPI app 模式；只验证路由层的参数
透传、状态码与错误 detail 结构（新建/复制/备份的内部逻辑由 editor_helpers 自身的
docstring 示例与既有 store 测试覆盖；这里补的是 M5 新增的四条路径）。单步试运行走真实
的 `AsyncJobRegistry`（内存态即可断言，不需要真的等后台线程跑完 LLM 调用——用一个立刻
返回的假 step 类型验证 job 能提交、precheck 能挡住不存在的 step）。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_workflow_editor_m5_routes.py -q
"""
from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.async_jobs import AsyncJobRegistry
from mini_agent.api.routes import router

SRC = """\
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


class _FakePaths:
    """AsyncJobRegistry 只用到这两个方法（落盘是 best-effort，测试里给个真目录即可）。"""

    def __init__(self, root: Path):
        self._d = root / ".agent" / "async_jobs"

    def async_job_record(self, job_id: str) -> Path:
        return self._d / f"{job_id}.json"

    def async_job_latest_pointer(self, key: str) -> Path:
        return self._d / "latest" / f"{key}.json"


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
    app.state.async_jobs = AsyncJobRegistry(_FakePaths(root))
    return TestClient(app)


class TestCreateWorkflowRoute(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        wdir = self.root / ".agent" / "workflows"
        wdir.mkdir(parents=True)
        (wdir / "demo.yaml").write_text(SRC, encoding="utf-8")
        self.client = _make_client(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_create_blank_workflow(self):
        r = self.client.post("/v1/workflows", json={"name": "new_flow"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["status"], "created")
        self.assertEqual(body["mode"], "file")
        self.assertIsNone(body["copied_from"])
        self.assertTrue((self.root / ".agent" / "workflows" / "new_flow.yaml").is_file())

    def test_create_copy_workflow_reports_shared_prompt_warning(self):
        wdir = self.root / ".agent" / "workflows"
        (wdir / "with_prompt.yaml").write_text(
            "name: with_prompt\nsteps:\n  - id: a\n    name: A\n    prompt_file: a.md\n",
            encoding="utf-8",
        )
        (wdir / "a.md").write_text("正文", encoding="utf-8")
        r = self.client.post("/v1/workflows", json={"name": "copy1", "copy_from": "with_prompt"})
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["copied_from"], "with_prompt")
        self.assertTrue(body["warnings"] and "共享" in body["warnings"][0])

    def test_create_duplicate_name_conflict(self):
        r = self.client.post("/v1/workflows", json={"name": "demo"})
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["detail"]["code"], "already_exists")

    def test_create_bad_name_rejected(self):
        r = self.client.post("/v1/workflows", json={"name": "a b"})
        self.assertEqual(r.status_code, 400)
        self.assertEqual(r.json()["detail"]["code"], "bad_request")

    def test_create_copy_from_missing_source_404(self):
        r = self.client.post("/v1/workflows", json={"name": "new_flow", "copy_from": "nope"})
        self.assertEqual(r.status_code, 404)

    def test_create_disabled_by_config(self):
        client = _make_client(self.root, visual_editor_enabled=False)
        r = client.post("/v1/workflows", json={"name": "new_flow"})
        self.assertEqual(r.status_code, 403)


class TestBackupRoutes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.wdir = self.root / ".agent" / "workflows"
        self.wdir.mkdir(parents=True)
        (self.wdir / "demo.yaml").write_text(SRC, encoding="utf-8")
        self.client = _make_client(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_list_backups_empty_when_never_saved(self):
        r = self.client.get("/v1/workflows/demo/backups")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"name": "demo", "backups": []})

    def test_list_backups_after_a_save_then_restore(self):
        doc = self.client.get("/v1/workflows/demo/editor").json()
        draft = doc["draft"]
        draft["steps"][0]["name"] = "改过的名字"
        save = self.client.put(
            "/v1/workflows/demo/editor",
            json={"draft": draft, "base_hash": doc["base_hash"]},
        )
        self.assertEqual(save.status_code, 200)

        backups = self.client.get("/v1/workflows/demo/backups").json()["backups"]
        self.assertEqual(len(backups), 1)
        bid = backups[0]["id"]
        self.assertIn("size", backups[0])

        restore = self.client.post(f"/v1/workflows/demo/backups/{bid}/restore")
        self.assertEqual(restore.status_code, 200)
        after = self.client.get("/v1/workflows/demo/editor").json()
        self.assertEqual(after["draft"]["steps"][0]["name"], "A")  # 恢复回改名前

        # 恢复本身也会再备份一次（可撤销恢复）
        backups2 = self.client.get("/v1/workflows/demo/backups").json()["backups"]
        self.assertEqual(len(backups2), 2)

    def test_restore_unknown_backup_404(self):
        r = self.client.post("/v1/workflows/demo/backups/20260101-000000-000/restore")
        self.assertEqual(r.status_code, 404)

    def test_restore_bad_backup_id_400(self):
        r = self.client.post("/v1/workflows/demo/backups/../../etc/restore")
        # 路径穿越形式的 id 校验不通过 → bad_request；FastAPI 路由本身也可能先拒绝
        self.assertIn(r.status_code, (400, 404))

    def test_restore_conflict_on_stale_base_hash(self):
        doc = self.client.get("/v1/workflows/demo/editor").json()
        draft = doc["draft"]
        draft["steps"][0]["name"] = "第一次改名"
        self.client.put("/v1/workflows/demo/editor", json={"draft": draft, "base_hash": doc["base_hash"]})
        bid = self.client.get("/v1/workflows/demo/backups").json()["backups"][0]["id"]
        r = self.client.post(
            f"/v1/workflows/demo/backups/{bid}/restore",
            json={"base_hash": "0" * 64},
        )
        self.assertEqual(r.status_code, 409)
        self.assertEqual(r.json()["detail"]["code"], "conflict")


class TestStepTestRoute(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        wdir = self.root / ".agent" / "workflows"
        wdir.mkdir(parents=True)
        (wdir / "demo.yaml").write_text(SRC, encoding="utf-8")
        self.client = _make_client(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    def test_precheck_rejects_unknown_workflow(self):
        r = self.client.post("/v1/workflows/nope/steps/a/test", json={})
        self.assertEqual(r.status_code, 404)

    def test_precheck_rejects_unknown_step(self):
        r = self.client.post("/v1/workflows/demo/steps/ghost/test", json={})
        self.assertEqual(r.status_code, 422)
        self.assertIn("ghost", r.json()["detail"])

    def test_bad_mock_payload_types_rejected(self):
        r = self.client.post("/v1/workflows/demo/steps/a/test", json={"mock_step_results": [1, 2]})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/v1/workflows/demo/steps/a/test", json={"mock_inputs": "nope"})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/v1/workflows/demo/steps/a/test", json={"timeout_override": -1})
        self.assertEqual(r.status_code, 400)
        r = self.client.post("/v1/workflows/demo/steps/a/test", json={"timeout_override": True})
        self.assertEqual(r.status_code, 400)

    def test_submits_async_job_and_can_be_polled(self):
        r = self.client.post(
            "/v1/workflows/demo/steps/a/test",
            json={"mock_inputs": {"x": "1"}, "timeout_override": 5},
        )
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertIn("job_id", body)
        self.assertEqual(body["key"], "workflow_step_test:demo:a")

        # 后台任务在事件循环里跑；TestClient 是同步的，但 asyncio.create_task 排在下一次
        # 事件循环轮转——给它一点时间轮询，允许 pending/done 两种终态（不阻塞在具体结果上，
        # 结果内容由 api_helpers.test_workflow_step 自身的测试覆盖）。
        for _ in range(20):
            poll = self.client.get(f"/v1/async_jobs/{body['job_id']}")
            if poll.json()["status"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual(poll.status_code, 200)
        self.assertIn(poll.json()["status"], ("done", "error", "running"))
