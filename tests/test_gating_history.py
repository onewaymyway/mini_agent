"""
tests/test_gating_history.py

覆盖 next_doc/scheduling_unification_and_kanban_visibility_improvement_plan.md
P5（全局日程 tab）配套新增的仲裁状态时间线：

- evolution/resource_arbiter.py::record_gating_transition() / read_gating_history()
- GET /v1/autonomous/gating_history

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_gating_history.py -q
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.storage.paths import AgentPaths
from mini_agent.evolution.resource_arbiter import record_gating_transition, read_gating_history
from mini_agent.api.routes import router


class TestRecordGatingTransition(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(project_root=Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_first_write_creates_entry(self):
        record_gating_transition(self.paths, "full", "正常")
        history = read_gating_history(self.paths)
        self.assertEqual(len(history), 1)
        self.assertEqual(history[0]["state"], "full")
        self.assertEqual(history[0]["reason"], "正常")
        self.assertIn("at", history[0])
        self.assertIn("at_str", history[0])

    def test_same_state_not_recorded_again(self):
        record_gating_transition(self.paths, "full", "正常")
        record_gating_transition(self.paths, "full", "正常")
        record_gating_transition(self.paths, "full", "正常")
        history = read_gating_history(self.paths)
        self.assertEqual(len(history), 1)

    def test_state_change_recorded(self):
        record_gating_transition(self.paths, "full", "正常")
        record_gating_transition(self.paths, "degraded", "挫败感升高")
        record_gating_transition(self.paths, "blocked", "预算耗尽")
        record_gating_transition(self.paths, "blocked", "预算耗尽")  # 重复不应再记
        record_gating_transition(self.paths, "full", "恢复正常")
        history = read_gating_history(self.paths)
        self.assertEqual([e["state"] for e in history], ["full", "degraded", "blocked", "full"])

    def test_read_history_empty_when_no_file(self):
        self.assertEqual(read_gating_history(self.paths), [])

    def test_read_history_respects_limit(self):
        for i in range(5):
            state = "full" if i % 2 == 0 else "degraded"
            record_gating_transition(self.paths, state, f"变化 {i}")
        history = read_gating_history(self.paths, limit=2)
        self.assertEqual(len(history), 2)
        # 保留的是最新的两条
        self.assertEqual(history[-1]["reason"], "变化 4")

    def test_corrupted_file_does_not_crash(self):
        self.paths.gating_history_path.parent.mkdir(parents=True, exist_ok=True)
        self.paths.gating_history_path.write_text("not json\n{broken", encoding="utf-8")
        # 读取时忽略损坏行，不抛异常
        history = read_gating_history(self.paths)
        self.assertEqual(history, [])
        # 写入时即使历史文件损坏也应能继续正常追加（视为无历史记录）
        record_gating_transition(self.paths, "full", "正常")


def _make_client(gating_state: str = "full", gating_reason: str = "正常", paths=None):
    app = FastAPI()
    app.include_router(router)
    cfg = SimpleNamespace(autonomy=SimpleNamespace())
    autonomous_loop = SimpleNamespace(_paths=paths, _cfg=cfg)
    bridge = SimpleNamespace(agent=SimpleNamespace(cfg=cfg))
    state = SimpleNamespace(bridge=bridge, autonomous_loop=autonomous_loop)
    app.state.http_server = state
    app.state.user_store = None

    def _fake_require_owner(request):
        return None

    import mini_agent.api.routes as routes_mod
    routes_mod._require_owner = _fake_require_owner
    return TestClient(app)


class TestGatingHistoryRoute(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.paths = AgentPaths(project_root=Path(self._tmp.name))

    def tearDown(self):
        self._tmp.cleanup()

    def test_empty_history_returns_empty_list(self):
        client = _make_client(paths=self.paths)
        resp = client.get("/v1/autonomous/gating_history")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"history": []})

    def test_history_reflects_recorded_transitions(self):
        record_gating_transition(self.paths, "full", "正常")
        record_gating_transition(self.paths, "blocked", "预算耗尽")
        client = _make_client(paths=self.paths)
        resp = client.get("/v1/autonomous/gating_history")
        self.assertEqual(resp.status_code, 200)
        history = resp.json()["history"]
        self.assertEqual([e["state"] for e in history], ["full", "blocked"])

    def test_no_autonomous_loop_returns_empty(self):
        app = FastAPI()
        app.include_router(router)
        state = SimpleNamespace(bridge=SimpleNamespace(agent=None), autonomous_loop=None)
        app.state.http_server = state
        app.state.user_store = None
        import mini_agent.api.routes as routes_mod
        routes_mod._require_owner = lambda request: None
        client = TestClient(app)
        resp = client.get("/v1/autonomous/gating_history")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"history": []})


if __name__ == "__main__":
    unittest.main()
