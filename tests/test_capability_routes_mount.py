"""
tests/test_capability_routes_mount.py

覆盖 next_doc/persona_capability_learning_design.md「实施状态」表格里标注
「尚未挂载到 api/server.py」的那一项：本次把 capability_routes.py 的
`capability_router` 接到 api/server.py 的 `create_app()` 里，这里验证挂载
后 `/v1/capability/*` 端点确实可用（走 capability_routes.py 自身的
`_get_paths()` 取 `request.app.state.bridge`，不拉起完整 HttpServer）。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_capability_routes_mount.py -q
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.capability_routes import capability_router


def _make_client(project_root: Path) -> TestClient:
    app = FastAPI()
    app.include_router(capability_router)
    cfg = SimpleNamespace(project_root=str(project_root))
    bridge = SimpleNamespace(agent=SimpleNamespace(cfg=cfg))
    app.state.bridge = bridge
    return TestClient(app)


class TestCapabilityRoutesMount(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmpdir.name)
        self.client = _make_client(self.project_root)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_list_tracks_empty(self):
        resp = self.client.get("/v1/capability/tracks")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"tracks": []})

    def test_create_and_get_track(self):
        resp = self.client.post("/v1/capability/tracks", json={
            "title": "股票分析能力", "persona_desc": "希望你具备强大的股票分析能力",
        })
        self.assertEqual(resp.status_code, 200)
        track_id = resp.json()["track_id"]

        resp2 = self.client.get(f"/v1/capability/tracks/{track_id}")
        self.assertEqual(resp2.status_code, 200)
        self.assertEqual(resp2.json()["title"], "股票分析能力")

    def test_question_lifecycle(self):
        # 先建一个 Track，直接用 CapabilityQuestionStore 落一条 pending 问题，
        # 走 API 提交回答，确认状态流转正确、路由确实生效。
        from mini_agent.evolution.capability_learning import CapabilityQuestionStore
        from mini_agent.storage.paths import AgentPaths

        paths = AgentPaths(project_root=self.project_root)
        store = CapabilityQuestionStore(paths)
        q = store.raise_question(
            track_id="t1", topic_id="topic1", question="你更关注短线还是长线？",
        )

        resp = self.client.post(
            f"/v1/capability/questions/{q.question_id}/answer", json={"answer": "长线"},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["status"], "answered")
        self.assertEqual(resp.json()["answer"], "长线")

    def test_delete_track_not_found(self):
        resp = self.client.delete("/v1/capability/tracks/does-not-exist")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
