"""
tests/test_capability_persona_wiki_scopes_binding.py

覆盖 next_doc/persona_capability_learning_design.md §11.4「看板知识范围
绑定」本次新增的两层实现：
  1. orchestrator/persona_profiles.py::list_personas_for_paths /
     set_persona_wiki_scopes（纯文件读写，不依赖 HTTP）
  2. api/capability_routes.py 新增的 GET /v1/capability/personas 与
     POST /v1/capability/personas/{name}/wiki_scopes 两个端点

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_capability_persona_wiki_scopes_binding.py -q
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.capability_routes import capability_router
from mini_agent.orchestrator.persona_profiles import (
    list_personas_for_paths,
    set_persona_wiki_scopes,
)
from mini_agent.storage.paths import AgentPaths

_PERSONA_MD = """---
name: stock-advisor
display_name: 老李投顾
description: 经验老道的资深投资顾问人设
tone: 犀利、老练
break_character_policy: soft
---

你是老李，一个经验丰富的投资顾问。
"""

_PERSONA_MD_NO_SCOPES_LINE = _PERSONA_MD  # 本身就没有 wiki_scopes 行，用于测试"插入"分支

_PERSONA_MD_WITH_SCOPES = """---
name: jarvis
display_name: 贾维斯
wiki_scopes: capability:old_tag
---

管家人设正文。
"""


def _make_client(project_root: Path) -> TestClient:
    app = FastAPI()
    app.include_router(capability_router)
    cfg = SimpleNamespace(project_root=str(project_root))
    bridge = SimpleNamespace(agent=SimpleNamespace(cfg=cfg))
    app.state.bridge = bridge
    return TestClient(app)


class TestPersonaProfilesWikiScopesUtils(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmpdir.name)
        self.paths = AgentPaths(self.project_root)
        self.paths.project_personas_dir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _write(self, name: str, text: str) -> Path:
        p = self.paths.project_personas_dir / f"{name}.md"
        p.write_text(text, encoding="utf-8")
        return p

    def test_list_personas_for_paths(self):
        self._write("stock-advisor", _PERSONA_MD)
        self._write("jarvis", _PERSONA_MD_WITH_SCOPES)
        personas = list_personas_for_paths(self.paths)
        names = sorted(p.name for p in personas)
        self.assertEqual(names, ["jarvis", "stock-advisor"])
        jarvis = next(p for p in personas if p.name == "jarvis")
        self.assertEqual(jarvis.wiki_scopes, ["capability:old_tag"])

    def test_set_wiki_scopes_inserts_when_missing(self):
        path = self._write("stock-advisor", _PERSONA_MD_NO_SCOPES_LINE)
        ok = set_persona_wiki_scopes(path, ["capability:stock_analysis"])
        self.assertTrue(ok)
        personas = list_personas_for_paths(self.paths)
        p = personas[0]
        self.assertEqual(p.wiki_scopes, ["capability:stock_analysis"])
        # 正文应保持不变
        self.assertIn("投资顾问", path.read_text(encoding="utf-8"))

    def test_set_wiki_scopes_replaces_existing_line(self):
        path = self._write("jarvis", _PERSONA_MD_WITH_SCOPES)
        ok = set_persona_wiki_scopes(path, ["capability:new_tag", "capability:another"])
        self.assertTrue(ok)
        p = list_personas_for_paths(self.paths)[0]
        self.assertEqual(p.wiki_scopes, ["capability:new_tag", "capability:another"])

    def test_set_wiki_scopes_empty_list_clears(self):
        path = self._write("jarvis", _PERSONA_MD_WITH_SCOPES)
        ok = set_persona_wiki_scopes(path, [])
        self.assertTrue(ok)
        p = list_personas_for_paths(self.paths)[0]
        self.assertEqual(p.wiki_scopes, [])

    def test_set_wiki_scopes_missing_frontmatter_fails(self):
        path = self.paths.project_personas_dir / "no_fm.md"
        path.write_text("没有 frontmatter 的纯正文", encoding="utf-8")
        ok = set_persona_wiki_scopes(path, ["x"])
        self.assertFalse(ok)


class TestCapabilityPersonaRoutes(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.project_root = Path(self._tmpdir.name)
        self.paths = AgentPaths(self.project_root)
        self.paths.project_personas_dir.mkdir(parents=True, exist_ok=True)
        (self.paths.project_personas_dir / "stock-advisor.md").write_text(_PERSONA_MD, encoding="utf-8")
        self.client = _make_client(self.project_root)

    def tearDown(self):
        self._tmpdir.cleanup()

    def test_list_personas_endpoint(self):
        resp = self.client.get("/v1/capability/personas")
        self.assertEqual(resp.status_code, 200)
        personas = resp.json()["personas"]
        self.assertEqual(len(personas), 1)
        self.assertEqual(personas[0]["name"], "stock-advisor")
        self.assertEqual(personas[0]["wiki_scopes"], [])

    def test_set_wiki_scopes_endpoint(self):
        resp = self.client.post(
            "/v1/capability/personas/stock-advisor/wiki_scopes",
            json={"wiki_scopes": ["capability:stock_analysis"]},
        )
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json()["wiki_scopes"], ["capability:stock_analysis"])

        # 再次列出应反映更新
        resp2 = self.client.get("/v1/capability/personas")
        personas = resp2.json()["personas"]
        self.assertEqual(personas[0]["wiki_scopes"], ["capability:stock_analysis"])

    def test_set_wiki_scopes_unknown_persona_404(self):
        resp = self.client.post(
            "/v1/capability/personas/does-not-exist/wiki_scopes",
            json={"wiki_scopes": ["x"]},
        )
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
