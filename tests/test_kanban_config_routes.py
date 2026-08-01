"""
tests/test_kanban_config_routes.py

覆盖 next_doc/kanban_config_management_plan.md 新增的 REST 端点：
  - GET   /v1/self/config   分类字段目录状态（agent_config.json）
  - PATCH /v1/self/config   批量更新 agent_config.json 里的若干字段

沿用 tests/test_goal_fairness_routes.py 的最小 FastAPI app 模式，不拉起
完整 HttpServer；cfg 用真实的 `load_config()` 产出（而不是 SimpleNamespace），
因为 config_catalog.build_status() 需要按 AppConfig 的完整嵌套结构取值。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_kanban_config_routes.py -q
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.routes import router
from mini_agent.config.loader import load_config


def _make_client(cfg) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    bridge = SimpleNamespace(agent=SimpleNamespace(cfg=cfg))
    app.state.http_server = SimpleNamespace(bridge=bridge)
    return TestClient(app)


class TestKanbanConfigRoutes(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.config_path = self.root / "agent_config.json"

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write_config(self, data: dict):
        self.config_path.write_text(json.dumps(data), encoding="utf-8")

    def _load_cfg(self):
        return load_config(config_file=self.config_path, project_root=self.root)

    def test_get_config_empty_file_shows_defaults(self):
        self._write_config({})
        cfg = self._load_cfg()
        client = _make_client(cfg)
        resp = client.get("/v1/self/config")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertGreater(len(data["categories"]), 10)
        core_cat = next(c for c in data["categories"] if c["id"] == "core")
        verbose_field = next(f for f in core_cat["fields"] if f["json_key"] == "verbose")
        self.assertFalse(verbose_field["customized"])
        self.assertEqual(verbose_field["value"], False)

    def test_get_config_reflects_customized_values(self):
        self._write_config({
            "verbose": True,
            "autonomy": {"max_concurrent_objectives_per_goal": 5},
        })
        cfg = self._load_cfg()
        client = _make_client(cfg)
        resp = client.get("/v1/self/config")
        data = resp.json()
        core_cat = next(c for c in data["categories"] if c["id"] == "core")
        verbose_field = next(f for f in core_cat["fields"] if f["json_key"] == "verbose")
        self.assertTrue(verbose_field["customized"])
        self.assertTrue(verbose_field["value"])

        autonomy_cat = next(c for c in data["categories"] if c["id"] == "autonomy")
        cap_field = next(
            f for f in autonomy_cat["fields"]
            if f["json_key"] == "autonomy.max_concurrent_objectives_per_goal"
        )
        self.assertEqual(cap_field["value"], 5)
        self.assertTrue(cap_field["customized"])

    def test_sensitive_fields_are_masked(self):
        self._write_config({"http_api_token": "super-secret-token"})
        cfg = self._load_cfg()
        client = _make_client(cfg)
        resp = client.get("/v1/self/config")
        data = resp.json()
        http_cat = next(c for c in data["categories"] if c["id"] == "http")
        token_field = next(f for f in http_cat["fields"] if f["json_key"] == "http_api_token")
        self.assertTrue(token_field["sensitive"])
        self.assertNotIn("super-secret-token", str(token_field["value"]))

    def test_patch_config_writes_file_and_is_reloadable(self):
        self._write_config({"verbose": False})
        cfg = self._load_cfg()
        client = _make_client(cfg)

        resp = client.patch("/v1/self/config", json={
            "updates": [
                {"json_key": "verbose", "value": True},
                {"json_key": "autonomy.max_concurrent_objectives_per_goal", "value": 7},
            ]
        })
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["restart_required"])

        # 文件确实被写入，且能被 load_config() 正常重新加载出预期值
        on_disk = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertTrue(on_disk["verbose"])
        self.assertEqual(on_disk["autonomy"]["max_concurrent_objectives_per_goal"], 7)

        reloaded = self._load_cfg()
        self.assertTrue(reloaded.verbose)
        self.assertEqual(reloaded.autonomy.max_concurrent_objectives_per_goal, 7)

    def test_patch_config_rejects_unknown_key_atomically(self):
        self._write_config({"verbose": False})
        cfg = self._load_cfg()
        client = _make_client(cfg)

        resp = client.patch("/v1/self/config", json={
            "updates": [
                {"json_key": "verbose", "value": True},
                {"json_key": "totally_not_a_real_key", "value": 1},
            ]
        })
        self.assertEqual(resp.status_code, 400)

        # 整批被拒绝，文件应该完全没有被改动（不是"verbose 生效了、后一条
        # 拒绝了"这种部分生效状态）。
        on_disk = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertEqual(on_disk, {"verbose": False})

    def test_patch_config_rejects_sensitive_key(self):
        self._write_config({})
        cfg = self._load_cfg()
        client = _make_client(cfg)

        resp = client.patch("/v1/self/config", json={
            "updates": [{"json_key": "http_api_token", "value": "hacked"}]
        })
        self.assertEqual(resp.status_code, 400)
        on_disk = json.loads(self.config_path.read_text(encoding="utf-8"))
        self.assertNotIn("http_api_token", on_disk)


if __name__ == "__main__":
    unittest.main()
