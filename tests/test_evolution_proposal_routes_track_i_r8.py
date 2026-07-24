"""
tests/test_evolution_proposal_routes_track_i_r8.py

覆盖 next_doc/kanban_and_autonomy_improvement_plan.md Track I 第八轮补齐的
看板可视化部分——新增的 REST 端点：

- GET  /v1/evolution/proposals                    列出提案分支及风险分级
- GET  /v1/evolution/proposals/{branch}/diff       某分支 diff 全文
- POST /v1/evolution/proposals/{branch}/merge      一键合并（risk=low 直接
  合并；risk=high 未传 force 时拒绝并返回 409；force=true 时合并）

不复用完整的 HttpServer（涉及很多与本轮改动无关的初始化），而是构造一个
最小的 FastAPI app，只挂载 `mini_agent.api.routes.router`，把
`app.state.http_server` 设成一个满足 `_evolution_state_repo()` 读取路径
（`http_server.bridge.agent.cfg.project_root`）的轻量 duck-typed 对象——
这与 routes.py 里其它端点实际读取 http_server 的方式完全一致，只是不需要
拉起整个 daemon。

运行方式：
    PYTHONPATH=src python3 -m pytest tests/test_evolution_proposal_routes_track_i_r8.py -q
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.evolution.state_repo import StateRepo
from mini_agent.api.routes import router


def _make_app(project_root: Path) -> TestClient:
    app = FastAPI()
    app.include_router(router)

    bridge = SimpleNamespace(agent=SimpleNamespace(cfg=SimpleNamespace(project_root=project_root)))
    app.state.http_server = SimpleNamespace(bridge=bridge)
    return TestClient(app)


class TrackIProposalRoutesBase(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.repo = StateRepo(self.root)
        self.repo.apply(changes={"README.md": "base\n"}, message="init", meta={}, tier="T0")
        self.main_branch = self.repo.current_branch()
        self.client = _make_app(self.root)

    def tearDown(self):
        self._tmpdir.cleanup()

    def _make_low_risk_branch(self, branch: str = "evolve/doc-fix") -> None:
        self.repo.create_branch(branch)
        self.repo._run_git(["checkout", branch])
        self.repo.apply(
            changes={"next_doc/some_doc.md": "hello low risk\n"},
            message="update doc", meta={}, tier="T1",
        )
        self.repo._run_git(["checkout", self.main_branch])

    def _make_high_risk_branch(self, branch: str = "evolve/core-change") -> None:
        self.repo.create_branch(branch)
        self.repo._run_git(["checkout", branch])
        self.repo.apply(
            changes={"src/core.py": "print('changed')\n"},
            message="touch core logic", meta={}, tier="T2",
        )
        self.repo._run_git(["checkout", self.main_branch])


class TestListEvolutionProposals(TrackIProposalRoutesBase):
    def test_empty_when_no_evolve_branches(self):
        resp = self.client.get("/v1/evolution/proposals")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["items"], [])
        self.assertEqual(body["count"], 0)

    def test_lists_low_and_high_risk_branches(self):
        self._make_low_risk_branch()
        self._make_high_risk_branch()
        resp = self.client.get("/v1/evolution/proposals")
        self.assertEqual(resp.status_code, 200)
        items = resp.json()["items"]
        self.assertEqual(len(items), 2)
        by_branch = {it["branch"]: it for it in items}
        self.assertEqual(by_branch["evolve/doc-fix"]["risk"], "low")
        self.assertEqual(by_branch["evolve/core-change"]["risk"], "high")
        # reasons/changed_paths 字段应该原样透出，供看板展示判定依据。
        self.assertTrue(by_branch["evolve/doc-fix"]["reasons"])
        self.assertIn("next_doc/some_doc.md", by_branch["evolve/doc-fix"]["changed_paths"])


class TestEvolutionProposalDiff(TrackIProposalRoutesBase):
    def test_diff_returns_unified_diff_text(self):
        self._make_low_risk_branch()
        resp = self.client.get("/v1/evolution/proposals/evolve/doc-fix/diff")
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertEqual(body["branch"], "evolve/doc-fix")
        self.assertIn("some_doc.md", body["diff"])

    def test_diff_404_for_unknown_branch(self):
        resp = self.client.get("/v1/evolution/proposals/evolve/does-not-exist/diff")
        self.assertEqual(resp.status_code, 404)


class TestMergeEvolutionProposal(TrackIProposalRoutesBase):
    def test_merge_low_risk_succeeds_without_force(self):
        self._make_low_risk_branch()
        resp = self.client.post("/v1/evolution/proposals/evolve/doc-fix/merge", json={})
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["risk"], "low")
        # 默认合并后删除源分支（StateRepo.merge_branch 的 delete_after 默认行为）。
        self.assertNotIn("evolve/doc-fix", self.repo.list_branches())

    def test_merge_high_risk_without_force_returns_409(self):
        self._make_high_risk_branch()
        resp = self.client.post("/v1/evolution/proposals/evolve/core-change/merge", json={})
        self.assertEqual(resp.status_code, 409)
        # 分支不应该被合并/删除。
        self.assertIn("evolve/core-change", self.repo.list_branches())

    def test_merge_high_risk_with_force_succeeds(self):
        self._make_high_risk_branch()
        resp = self.client.post(
            "/v1/evolution/proposals/evolve/core-change/merge", json={"force": True},
        )
        self.assertEqual(resp.status_code, 200)
        body = resp.json()
        self.assertTrue(body["ok"])
        self.assertEqual(body["risk"], "high")
        self.assertNotIn("evolve/core-change", self.repo.list_branches())

    def test_merge_unknown_branch_returns_404(self):
        resp = self.client.post("/v1/evolution/proposals/evolve/nope/merge", json={})
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
