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

    def test_create_track_with_llm_draft_no_helper_falls_back_empty(self):
        # bridge.agent 没有 llm_helper 属性，_get_llm_helper 应该返回
        # None，create_track 端点静默退回空大纲，不报错。
        resp = self.client.post("/v1/capability/tracks", json={
            "title": "股票分析能力", "persona_desc": "x", "llm_draft": True,
        })
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["outline"], [])

    def test_create_track_with_llm_draft_uses_helper(self):
        self.client.app.state.bridge.agent.llm_helper = SimpleNamespace(
            ask=lambda prompt: "基础概念\n技术分析\n基本面分析\n风险管理",
        )
        resp = self.client.post("/v1/capability/tracks", json={
            "title": "股票分析能力", "persona_desc": "x", "llm_draft": True,
        })
        self.assertEqual(resp.status_code, 200)
        names = [t["name"] for t in resp.json()["outline"]]
        self.assertEqual(names, ["基础概念", "技术分析", "基本面分析", "风险管理"])


class TestPersonaDraftRoutes(unittest.TestCase):
    """§10.3 persona 型 Track 人设草稿三个端点：draft/get/publish。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmpdir.name)
        self.client = _make_client(self.project_root)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _create_persona_track(self, outline_names=None):
        resp = self.client.post("/v1/capability/tracks", json={
            "title": "老李投顾", "persona_desc": "经验老道的资深投资顾问人设",
            "outline_names": outline_names or ["身份背景", "说话习惯"],
            "target_type": "persona",
        })
        self.assertEqual(resp.status_code, 200)
        return resp.json()["track_id"]

    def test_draft_endpoint_rejects_knowledge_type_track(self):
        resp = self.client.post("/v1/capability/tracks", json={
            "title": "股票分析", "persona_desc": "x",
        })
        track_id = resp.json()["track_id"]
        resp2 = self.client.post(f"/v1/capability/tracks/{track_id}/persona/draft")
        self.assertEqual(resp2.status_code, 400)

    def test_draft_endpoint_not_found_track(self):
        resp = self.client.post("/v1/capability/tracks/does-not-exist/persona/draft")
        self.assertEqual(resp.status_code, 404)

    def test_get_draft_before_generated_returns_404(self):
        track_id = self._create_persona_track()
        resp = self.client.get(f"/v1/capability/tracks/{track_id}/persona/draft")
        self.assertEqual(resp.status_code, 404)

    def test_publish_before_draft_returns_400(self):
        track_id = self._create_persona_track()
        resp = self.client.post(f"/v1/capability/tracks/{track_id}/persona/publish")
        self.assertEqual(resp.status_code, 400)

    def test_draft_show_publish_full_flow(self):
        track_id = self._create_persona_track()

        # 先用 answer 端点回答一个问题，验证草稿会包含这条回答
        from mini_agent.evolution.capability_learning import CapabilityQuestionStore, CapabilityTrackStore
        from mini_agent.storage.paths import AgentPaths

        paths = AgentPaths(project_root=self.project_root)
        track = CapabilityTrackStore(paths).get(track_id)
        topic_id = track.outline[0].topic_id
        q_store = CapabilityQuestionStore(paths)
        q = q_store.raise_question(track_id, topic_id, "你的从业背景是什么？")
        self.client.post(f"/v1/capability/questions/{q.question_id}/answer", json={"answer": "券商工作15年"})

        draft_resp = self.client.post(f"/v1/capability/tracks/{track_id}/persona/draft")
        self.assertEqual(draft_resp.status_code, 200)
        body = draft_resp.json()
        self.assertIn("券商工作15年", body["draft"])
        self.assertEqual(body["completeness"]["total"], 2)
        self.assertEqual(body["completeness"]["answered"], 1)

        get_resp = self.client.get(f"/v1/capability/tracks/{track_id}/persona/draft")
        self.assertEqual(get_resp.status_code, 200)
        self.assertEqual(get_resp.json()["draft"], body["draft"])

        publish_resp = self.client.post(f"/v1/capability/tracks/{track_id}/persona/publish")
        self.assertEqual(publish_resp.status_code, 200)
        published_path = Path(publish_resp.json()["published_path"])
        self.assertTrue(published_path.exists())
        self.assertEqual(published_path.parent, paths.project_personas_dir)


class TestCapabilityOutlineSuggestionRoutes(unittest.TestCase):
    """v0.21 §13.2-f 大纲动态生长建议端点挂载测试。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmpdir.name)
        self.client = _make_client(self.project_root)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_list_suggestions_empty(self):
        resp = self.client.get("/v1/capability/suggestions")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"suggestions": []})

    def test_accept_suggestion_adds_topic(self):
        from mini_agent.evolution.capability_learning import (
            CapabilityOutlineSuggestionStore,
            CapabilityTrackStore,
            OutlineSuggestion,
        )
        from mini_agent.storage.paths import AgentPaths

        paths = AgentPaths(project_root=self.project_root)
        track = CapabilityTrackStore(paths).create(title="股票分析", persona_desc="股票分析")
        CapabilityOutlineSuggestionStore(paths).add(OutlineSuggestion(
            suggestion_id="capsug_x1", track_id=track.track_id,
            source_question_id="q1", suggested_name="港股市场特点",
        ))

        resp = self.client.post("/v1/capability/suggestions/capsug_x1/accept")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["topic"]["name"], "港股市场特点")

        list_resp = self.client.get("/v1/capability/suggestions", params={"status": "pending"})
        self.assertEqual(list_resp.json(), {"suggestions": []})

    def test_dismiss_suggestion(self):
        from mini_agent.evolution.capability_learning import (
            CapabilityOutlineSuggestionStore,
            CapabilityTrackStore,
            OutlineSuggestion,
        )
        from mini_agent.storage.paths import AgentPaths

        paths = AgentPaths(project_root=self.project_root)
        track = CapabilityTrackStore(paths).create(title="股票分析", persona_desc="股票分析")
        CapabilityOutlineSuggestionStore(paths).add(OutlineSuggestion(
            suggestion_id="capsug_x2", track_id=track.track_id,
            source_question_id="q1", suggested_name="港股市场特点",
        ))

        resp = self.client.post("/v1/capability/suggestions/capsug_x2/dismiss")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json(), {"dismissed": True, "suggestion_id": "capsug_x2"})

    def test_accept_unknown_suggestion_404(self):
        resp = self.client.post("/v1/capability/suggestions/does_not_exist/accept")
        self.assertEqual(resp.status_code, 404)

    def test_dismiss_unknown_suggestion_404(self):
        resp = self.client.post("/v1/capability/suggestions/does_not_exist/dismiss")
        self.assertEqual(resp.status_code, 404)


class TestCapabilityWikiPageRoute(unittest.TestCase):
    """看板"能力大纲覆盖状态"区块直接查看关联 wiki 页面用的端点。"""

    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmpdir.name)
        self.client = _make_client(self.project_root)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_get_existing_page_returns_body(self):
        from mini_agent.storage.paths import AgentPaths
        from mini_agent.wiki.writer import write_page

        paths = AgentPaths(project_root=self.project_root)
        write_page(paths, page_id="testpage1", page_type="topic", body="这是测试内容")

        resp = self.client.get("/v1/capability/wiki_pages/testpage1")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["page_id"], "testpage1")
        self.assertIn("这是测试内容", data["body"])

    def test_get_unknown_page_404(self):
        resp = self.client.get("/v1/capability/wiki_pages/does_not_exist")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
