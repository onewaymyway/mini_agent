"""tests/test_hybrid_exec_summary_route.py — hybrid_exec 只读汇总端点
GET /v1/hybrid_exec/summary 测试。

风格对齐 tests/test_feedback_loop_summary_route.py：不拉起完整
HttpServer，只挂 router 到一个最小 FastAPI app。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.routes import router
from mini_agent.hybrid_exec.recorder import RunRecorder
from mini_agent.hybrid_exec.repository import ScriptRepository
from mini_agent.hybrid_exec.spec import ExecutionResult, ExecutionTier


def _make_app(project_root: Path) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    bridge = SimpleNamespace(agent=SimpleNamespace(cfg=SimpleNamespace(project_root=project_root)))
    app.state.http_server = SimpleNamespace(bridge=bridge, autonomous_loop=None)
    return TestClient(app)


class TestHybridExecSummaryRoute(unittest.TestCase):
    def test_empty_project_returns_empty_task_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            client = _make_app(Path(tmp))
            resp = client.get("/v1/hybrid_exec/summary")
            self.assertEqual(resp.status_code, 200)
            self.assertEqual(resp.json(), {"tasks": []})

    def test_returns_aggregated_task_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_root = Path(tmp)
            repo = ScriptRepository(project_root / ".agent" / "hybrid_exec" / "scripts")
            repo.save_new_version("extract_entities_v1", "code", "llm_explorer")
            repo.record_success("extract_entities_v1", 1)

            recorder = RunRecorder(project_root / ".agent" / "hybrid_exec" / "runs")
            recorder.record(
                "extract_entities_v1",
                ExecutionResult(ok=True, output="ok", tier_used=ExecutionTier.SCRIPT, script_version=1),
            )

            client = _make_app(project_root)
            resp = client.get("/v1/hybrid_exec/summary")
            self.assertEqual(resp.status_code, 200)
            data = resp.json()
            self.assertEqual(len(data["tasks"]), 1)
            task = data["tasks"][0]
            self.assertEqual(task["task_id"], "extract_entities_v1")
            self.assertEqual(task["active_version"], 1)
            self.assertEqual(task["run_summary"]["total_runs"], 1)

    def test_missing_http_server_returns_503(self):
        app = FastAPI()
        app.include_router(router)
        client = TestClient(app)
        resp = client.get("/v1/hybrid_exec/summary")
        self.assertEqual(resp.status_code, 503)


if __name__ == "__main__":
    unittest.main()
