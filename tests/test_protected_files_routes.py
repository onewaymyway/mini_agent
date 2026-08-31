"""
tests/test_protected_files_routes.py — 看板"受保护文件清单"UI 对应的
REST 端点测试。

沿用 tests/test_kanban_config_routes.py 的最小 FastAPI app 模式（不拉起
完整 HttpServer），只需要 `app.state.project_root`，因为这组端点全部
基于 project_root 直接操作 scripts/protected_files.py +
evolution/protected_files_backup.py，不依赖 agent/session。

覆盖：
  - GET    /v1/protected-files/status              空清单 / 有清单
  - POST   /v1/protected-files/entries              新增声明 + 幂等
  - DELETE /v1/protected-files/entries              删除声明 / 不能删清单自身
  - POST   /v1/protected-files/backup               手动触发备份
  - GET    /v1/protected-files/snapshots            快照列表
  - GET    /v1/protected-files/snapshots/{gen_id}   快照详情 / 404
  - POST   /v1/protected-files/restore              force=False 只预览 /
                                                     force=True 才真正恢复
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.routes import router
from scripts.protected_files import MANIFEST_FILENAME


def _make_client(project_root: Path) -> TestClient:
    app = FastAPI()
    app.include_router(router)
    app.state.project_root = project_root
    return TestClient(app)


class TestProtectedFilesRoutes(unittest.TestCase):
    def setUp(self):
        self._tmpdir = tempfile.TemporaryDirectory()
        self.root = Path(self._tmpdir.name)
        self.client = _make_client(self.root)

    def tearDown(self):
        self._tmpdir.cleanup()

    # ── status ──────────────────────────────────────────────────────────

    def test_status_empty(self):
        resp = self.client.get("/v1/protected-files/status")
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(data["entries"], [])
        self.assertEqual(data["snapshot_count"], 0)

    # ── add / remove entries ───────────────────────────────────────────

    def test_add_entry_creates_manifest(self):
        (self.root / "a.txt").write_text("x", encoding="utf-8")
        resp = self.client.post("/v1/protected-files/entries", json={"path": "a.txt"})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["entries"]), 2)  # 清单文件自身 + a.txt
        paths = [e["path"] for e in data["entries"]]
        self.assertTrue(any(p.endswith("a.txt") for p in paths))

    def test_add_entry_idempotent(self):
        (self.root / "a.txt").write_text("x", encoding="utf-8")
        self.client.post("/v1/protected-files/entries", json={"path": "a.txt"})
        resp = self.client.post("/v1/protected-files/entries", json={"path": "a.txt"})
        data = resp.json()
        self.assertEqual(len(data["entries"]), 2)  # 不重复

    def test_add_entry_missing_path_400(self):
        resp = self.client.post("/v1/protected-files/entries", json={"path": ""})
        self.assertEqual(resp.status_code, 400)

    def test_remove_entry(self):
        (self.root / "a.txt").write_text("x", encoding="utf-8")
        self.client.post("/v1/protected-files/entries", json={"path": "a.txt"})
        target = str((self.root / "a.txt").resolve())
        resp = self.client.request("DELETE", "/v1/protected-files/entries", json={"path": target})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertEqual(len(data["entries"]), 1)  # 只剩清单文件自身

    def test_remove_entry_not_found_404(self):
        resp = self.client.request(
            "DELETE", "/v1/protected-files/entries", json={"path": str(self.root / "nope.txt")}
        )
        self.assertEqual(resp.status_code, 404)

    def test_remove_manifest_itself_404(self):
        (self.root / "a.txt").write_text("x", encoding="utf-8")
        self.client.post("/v1/protected-files/entries", json={"path": "a.txt"})
        manifest_path = str((self.root / MANIFEST_FILENAME).resolve())
        resp = self.client.request("DELETE", "/v1/protected-files/entries", json={"path": manifest_path})
        self.assertEqual(resp.status_code, 404)

    # ── backup / snapshots ──────────────────────────────────────────────

    def test_backup_and_list_snapshots(self):
        (self.root / "a.txt").write_text("x", encoding="utf-8")
        self.client.post("/v1/protected-files/entries", json={"path": "a.txt"})

        resp = self.client.post("/v1/protected-files/backup", json={"keep_count": 5})
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["generation_id"])
        self.assertEqual(data["errors"], [])

        resp2 = self.client.get("/v1/protected-files/snapshots")
        self.assertEqual(resp2.status_code, 200)
        snapshots = resp2.json()
        self.assertEqual(len(snapshots), 1)
        self.assertEqual(snapshots[0]["generation_id"], data["generation_id"])

    def test_snapshot_detail_and_404(self):
        (self.root / "a.txt").write_text("x", encoding="utf-8")
        self.client.post("/v1/protected-files/entries", json={"path": "a.txt"})
        backup_resp = self.client.post("/v1/protected-files/backup", json={"keep_count": 5})
        gen_id = backup_resp.json()["generation_id"]

        resp = self.client.get(f"/v1/protected-files/snapshots/{gen_id}")
        self.assertEqual(resp.status_code, 200)
        detail = resp.json()
        self.assertTrue(any(p.endswith("a.txt") for p in detail["paths"]))

        resp404 = self.client.get("/v1/protected-files/snapshots/does_not_exist")
        self.assertEqual(resp404.status_code, 404)

    # ── restore ─────────────────────────────────────────────────────────

    def test_restore_preview_without_force(self):
        (self.root / "a.txt").write_text("original", encoding="utf-8")
        self.client.post("/v1/protected-files/entries", json={"path": "a.txt"})
        gen_id = self.client.post(
            "/v1/protected-files/backup", json={"keep_count": 5}
        ).json()["generation_id"]

        (self.root / "a.txt").write_text("corrupted", encoding="utf-8")

        resp = self.client.post(
            "/v1/protected-files/restore",
            json={"generation_id": gen_id, "paths": [], "force": False},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["preview"])
        self.assertEqual(data["restored"], [])
        # 没有真的写盘
        self.assertEqual((self.root / "a.txt").read_text(encoding="utf-8"), "corrupted")

    def test_restore_with_force_writes(self):
        (self.root / "a.txt").write_text("original", encoding="utf-8")
        self.client.post("/v1/protected-files/entries", json={"path": "a.txt"})
        gen_id = self.client.post(
            "/v1/protected-files/backup", json={"keep_count": 5}
        ).json()["generation_id"]

        (self.root / "a.txt").write_text("corrupted", encoding="utf-8")

        resp = self.client.post(
            "/v1/protected-files/restore",
            json={"generation_id": gen_id, "paths": [], "force": True},
        )
        self.assertEqual(resp.status_code, 200)
        data = resp.json()
        self.assertTrue(data["restored"])
        self.assertEqual((self.root / "a.txt").read_text(encoding="utf-8"), "original")

    def test_restore_unknown_snapshot_404(self):
        resp = self.client.post(
            "/v1/protected-files/restore",
            json={"generation_id": "nope", "paths": [], "force": True},
        )
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
