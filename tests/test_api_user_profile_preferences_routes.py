"""tests/test_api_user_profile_preferences_routes.py

覆盖 next_doc/profile_context_sources_completeness_plan.md 方向 D 收尾：
看板可视化编辑用到的三个 REST 端点：

  - GET  /v1/user_profile/preferences               读取
  - POST /v1/user_profile/preferences                新增/覆盖一条
  - POST /v1/user_profile/preferences/delete         删除一条

风格对齐 tests/test_evolution_proposal_routes_track_i_r8.py：不拉起完整
HttpServer，只挂 router 到一个最小 FastAPI app，`app.state.http_server`
用 duck-typed `SimpleNamespace` 满足 `_get_paths_for_request()` 需要的
`http_server.bridge.agent.cfg.project_root`。

这三个端点跟 CLI 的 `/profile set|unset|show` 是同一份数据、同一条写入
路径（`UserProfileManager`），这里只验证 HTTP 层的行为，不重复覆盖
`UserProfileManager` 本身的单测（见 test_profile.py）。
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.routes import router
from mini_agent.profile import UserProfileManager
from mini_agent.storage.paths import AgentPaths


def _make_app(project_root: Path) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    bridge = SimpleNamespace(agent=SimpleNamespace(cfg=SimpleNamespace(project_root=project_root)))
    app.state.http_server = SimpleNamespace(bridge=bridge)
    return TestClient(app)


class TestUserProfilePreferencesRoutes(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.paths = AgentPaths(self.root)
        self.client = _make_app(self.root)

    def tearDown(self):
        self._tmp.cleanup()

    # ── GET ──────────────────────────────────────────────────────────

    def test_get_empty_preferences(self):
        resp = self.client.get("/v1/user_profile/preferences")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"preferences": {}})

    def test_get_reflects_existing_preferences(self):
        UserProfileManager(self.paths).set_preference("tone", "casual")
        resp = self.client.get("/v1/user_profile/preferences")
        self.assertEqual(resp.json(), {"preferences": {"tone": "casual"}})

    # ── POST set ─────────────────────────────────────────────────────

    def test_post_set_creates_preference(self):
        resp = self.client.post(
            "/v1/user_profile/preferences", json={"key": "tone", "value": "casual"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "key": "tone", "value": "casual"})
        profile = UserProfileManager(self.paths).load()
        self.assertEqual(profile.preferences.get("tone"), "casual")

    def test_post_set_overwrites_existing_value(self):
        UserProfileManager(self.paths).set_preference("tone", "casual")
        resp = self.client.post(
            "/v1/user_profile/preferences", json={"key": "tone", "value": "formal"}
        )
        self.assertEqual(resp.status_code, 200)
        profile = UserProfileManager(self.paths).load()
        self.assertEqual(profile.preferences.get("tone"), "formal")

    def test_post_set_empty_key_returns_400(self):
        resp = self.client.post(
            "/v1/user_profile/preferences", json={"key": "  ", "value": "x"}
        )
        self.assertEqual(resp.status_code, 400)

    def test_post_set_missing_value_returns_422(self):
        resp = self.client.post("/v1/user_profile/preferences", json={"key": "tone"})
        self.assertEqual(resp.status_code, 422)

    # ── POST delete ──────────────────────────────────────────────────

    def test_post_delete_existing_key(self):
        UserProfileManager(self.paths).set_preference("tone", "casual")
        resp = self.client.post(
            "/v1/user_profile/preferences/delete", json={"key": "tone"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "key": "tone", "existed": True})
        profile = UserProfileManager(self.paths).load()
        self.assertNotIn("tone", profile.preferences)

    def test_post_delete_missing_key_reports_existed_false(self):
        resp = self.client.post(
            "/v1/user_profile/preferences/delete", json={"key": "nope"}
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"ok": True, "key": "nope", "existed": False})

    # ── CLI/API 共享同一份数据 ────────────────────────────────────────

    def test_shares_storage_with_cli_write_path(self):
        """API 写入后，直接用 UserProfileManager（CLI 走的同一条路径）
        应该能看到同样的数据——两边不是各自独立的存储。"""
        self.client.post(
            "/v1/user_profile/preferences", json={"key": "lang", "value": "zh"}
        )
        mgr = UserProfileManager(self.paths)
        self.assertEqual(mgr.load().preferences.get("lang"), "zh")


if __name__ == "__main__":
    unittest.main()
