"""tests/test_session_cleanup_external_projects.py — 会话清理支持外部项目 +
联动清理 raw_results（next_doc/session_cleanup_external_projects_plan.md）。

覆盖：
  1. raw_results 联动：session / 孤儿目录被删时对应 raw_results/<id>/ 一并清理；
     dry-run 只统计不删；被保留的 session 的 raw_results 不动；非法 id 不会指向别处。
  2. 外部项目会话环境解析：未注册 / 目录缺失 / 无会话目录 / 成功，且不会创建目录。
  3. 外部项目判定与主项目同规则（pinned / goal 在跑 / 最近窗口），且两个项目互相隔离。
  4. HTTP 路由：project=<名> / all_external_projects / 互斥校验 / 404 / scopes。
  5. agent 经 run_slash_command 不能触发外部项目范围的清理。

风格对齐 tests/test_api_external_projects_routes.py：不拉起完整 HttpServer，
只挂 router 到最小 FastAPI app，注册表存储路径指向临时目录。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.routes import router
from mini_agent.evolution.session_cleanup import (
    ExternalProjectSessionError,
    _raw_results_dir_for,
    cleanup_sessions,
    resolve_external_project_session_env,
)
from mini_agent.external_projects.registry import ExternalProjectRegistry
from mini_agent.session import SessionManager


# ── 构造辅助 ──────────────────────────────────────────────────────────────

def _iso(days_ago: float) -> str:
    return (datetime.now() - timedelta(days=days_ago)).isoformat(timespec="seconds")


def _make_session(sessions_dir: Path, sid: str, *, days_ago: float = 100, turns: int = 1,
                  pinned: bool = False, extracted: bool = False) -> Path:
    d = sessions_dir / sid
    d.mkdir(parents=True, exist_ok=True)
    meta = {
        "id": sid, "title": f"title-{sid}",
        "created_at": _iso(days_ago), "updated_at": _iso(days_ago),
        "provider": "p", "model": "m",
        "stats": {"turns": turns, "input_tokens": 0, "output_tokens": 0, "tool_calls": 0},
    }
    if pinned:
        meta["pinned"] = True
    if extracted:
        meta["knowledge_extracted"] = True
    (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    (d / "history.json").write_text("[]", encoding="utf-8")
    return d


def _make_raw_results(project_root: Path, sid: str, payload: str = "x" * 2048) -> Path:
    d = project_root / ".agent" / "raw_results" / sid
    d.mkdir(parents=True, exist_ok=True)
    (d / "abc.txt").write_text(payload, encoding="utf-8")
    (d / "abc.meta.json").write_text("{}", encoding="utf-8")
    return d


def _sessions_dir(project_root: Path) -> Path:
    return project_root / ".agent" / "sessions"


def _names(sessions_dir: Path) -> set:
    """sessions 目录下的会话目录名（忽略 SessionManager 自己维护的 .backfill_index_gen 等点文件）。"""
    return {p.name for p in sessions_dir.iterdir() if p.is_dir()}


def _clean(project_root: Path, **kw):
    kw.setdefault("keep_recent_days", 30)
    kw.setdefault("keep_recent_count", 0)
    kw.setdefault("dry_run", False)
    mgr = SessionManager(session_dir=_sessions_dir(project_root))
    return cleanup_sessions(mgr, project_root, exclude_ids=set(), **kw)


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()


# ── 1. raw_results 联动 ───────────────────────────────────────────────────

class TestRawResultsLinkage(_Base):
    def test_deleted_session_takes_its_raw_results(self):
        root = self.tmp / "proj"
        sd = _sessions_dir(root)
        _make_session(sd, "old1")
        _make_session(sd, "new1", days_ago=1)
        rr_old = _make_raw_results(root, "old1")
        rr_new = _make_raw_results(root, "new1")

        report = _clean(root)

        self.assertEqual([i.session_id for i in report.deleted], ["old1"])
        self.assertFalse((sd / "old1").exists())
        self.assertFalse(rr_old.exists(), "被删 session 的 raw_results 应一并清理")
        self.assertTrue((sd / "new1").exists())
        self.assertTrue(rr_new.exists(), "被保留 session 的 raw_results 不能动")
        self.assertGreater(report.deleted[0].raw_results_bytes, 0)
        self.assertEqual(report.raw_results_total_bytes, report.deleted[0].raw_results_bytes)

    def test_dry_run_reports_size_but_deletes_nothing(self):
        root = self.tmp / "proj"
        sd = _sessions_dir(root)
        _make_session(sd, "old1")
        rr = _make_raw_results(root, "old1")

        report = _clean(root, dry_run=True)

        self.assertEqual(len(report.deleted), 1)
        self.assertGreater(report.deleted[0].raw_results_bytes, 0)
        self.assertTrue((sd / "old1").exists())
        self.assertTrue(rr.exists())

    def test_session_without_raw_results_is_fine(self):
        root = self.tmp / "proj"
        _make_session(_sessions_dir(root), "old1")
        report = _clean(root)
        self.assertEqual(len(report.deleted), 1)
        self.assertEqual(report.deleted[0].raw_results_bytes, 0)

    def test_orphan_dir_takes_its_raw_results(self):
        import os
        import time
        root = self.tmp / "proj"
        sd = _sessions_dir(root)
        _make_session(sd, "keepme", days_ago=1)  # 让 sessions 目录存在
        orphan = sd / "orph1"
        orphan.mkdir()
        f = orphan / "raw_history.jsonl"
        f.write_text("{}", encoding="utf-8")
        old = time.time() - 48 * 3600
        os.utime(f, (old, old))
        os.utime(orphan, (old, old))
        rr = _make_raw_results(root, "orph1")

        report = _clean(root, include_orphans=True, orphan_min_age_hours=6)

        self.assertEqual([i.dir_name for i in report.orphan_deleted], ["orph1"])
        self.assertFalse(orphan.exists())
        self.assertFalse(rr.exists())
        self.assertGreater(report.orphan_deleted[0].raw_results_bytes, 0)

    def test_unsafe_session_id_never_resolves_outside_raw_results(self):
        root = self.tmp / "proj"
        _make_raw_results(root, "legit")
        (root / ".agent" / "victim").mkdir(parents=True)
        for bad in ("..", ".", "", "../victim", "a/b", "legit/.."):
            self.assertIsNone(_raw_results_dir_for(root, bad), bad)
        self.assertIsNotNone(_raw_results_dir_for(root, "legit"))
        self.assertTrue((root / ".agent" / "victim").exists())


# ── 2. 外部项目会话环境解析 ─────────────────────────────────────────────

class TestResolveExternalEnv(_Base):
    def setUp(self):
        super().setUp()
        self.registry = ExternalProjectRegistry(self.tmp / "registry.json")

    def _register(self, name: str, with_sessions: bool = True) -> Path:
        root = self.tmp / name
        root.mkdir()
        (root / "project.yaml").write_text("name: x\nentrypoints: {}\n", encoding="utf-8")
        if with_sessions:
            _sessions_dir(root).mkdir(parents=True)
        # 绕过 manifest 校验，直接写注册表
        self.registry.register(name, root, validate=False)
        return root

    def test_not_registered(self):
        with self.assertRaises(ExternalProjectSessionError) as cm:
            resolve_external_project_session_env("nope", self.registry)
        self.assertEqual(cm.exception.kind, "not_registered")

    def test_root_missing(self):
        root = self._register("gone")
        import shutil
        shutil.rmtree(root)
        with self.assertRaises(ExternalProjectSessionError) as cm:
            resolve_external_project_session_env("gone", self.registry)
        self.assertEqual(cm.exception.kind, "root_missing")

    def test_no_sessions_dir_and_does_not_create_it(self):
        root = self._register("fresh", with_sessions=False)
        with self.assertRaises(ExternalProjectSessionError) as cm:
            resolve_external_project_session_env("fresh", self.registry)
        self.assertEqual(cm.exception.kind, "no_sessions_dir")
        self.assertFalse(_sessions_dir(root).exists(), "解析阶段不能顺手创建 sessions 目录")

    def test_success(self):
        root = self._register("ok")
        mgr, resolved_root, sdir = resolve_external_project_session_env("ok", self.registry)
        self.assertEqual(resolved_root, root.resolve())
        self.assertEqual(sdir, _sessions_dir(root.resolve()))
        self.assertEqual(Path(mgr.session_dir), sdir)

    def test_disabled_project_can_still_be_cleaned(self):
        self._register("off")  # register() 默认 enabled=False
        self.assertFalse(self.registry.get("off").enabled)
        resolve_external_project_session_env("off", self.registry)  # 不抛


# ── 3. 判定规则一致 + 项目隔离 ─────────────────────────────────────────

class TestExternalRulesAndIsolation(_Base):
    def test_same_rules_apply_and_projects_are_isolated(self):
        a, b = self.tmp / "a", self.tmp / "b"
        for root in (a, b):
            sd = _sessions_dir(root)
            _make_session(sd, "old", days_ago=100)
            _make_session(sd, "pinned", days_ago=100, pinned=True)
            _make_session(sd, "goal", days_ago=100)
            (sd / "goal" / "goal_state.json").write_text(
                json.dumps({"status": "running", "goal_spec": {"goal_text": "g"}}), encoding="utf-8")
            _make_session(sd, "recent", days_ago=2)
            _make_raw_results(root, "old")
            _make_raw_results(root, "pinned")

        report = _clean(a)

        self.assertEqual({i.session_id for i in report.deleted}, {"old"})
        sd_a, sd_b = _sessions_dir(a), _sessions_dir(b)
        self.assertEqual(_names(sd_a), {"pinned", "goal", "recent"})
        self.assertTrue((a / ".agent" / "raw_results" / "pinned").exists())
        # b 项目一个字节都不能动
        self.assertEqual(_names(sd_b), {"old", "pinned", "goal", "recent"})
        self.assertTrue((b / ".agent" / "raw_results" / "old").exists())

    def test_unextracted_big_session_is_skipped_not_deleted(self):
        root = self.tmp / "p"
        _make_session(_sessions_dir(root), "big", days_ago=100, turns=10)
        _make_session(_sessions_dir(root), "done", days_ago=100, turns=10, extracted=True)
        _make_raw_results(root, "big")
        report = _clean(root)
        self.assertEqual({i.session_id for i in report.deleted}, {"done"})
        self.assertEqual({i.session_id for i in report.skipped_pending_extraction}, {"big"})
        self.assertTrue((_sessions_dir(root) / "big").exists())
        self.assertTrue((root / ".agent" / "raw_results" / "big").exists())


# ── 4. HTTP 路由 ───────────────────────────────────────────────────────

class TestCleanupRoutes(_Base):
    def setUp(self):
        super().setUp()
        import mini_agent.external_projects.registry as registry_mod
        self.registry_path = self.tmp / "registry.json"
        self._registry_mod = registry_mod
        self._orig = registry_mod.DEFAULT_REGISTRY_PATH
        registry_mod.DEFAULT_REGISTRY_PATH = self.registry_path
        self.registry = ExternalProjectRegistry(self.registry_path)

        app = FastAPI()
        app.include_router(router)
        app.state.http_server = None
        self.client = TestClient(app)

        self.p1 = self._project("p1")
        self.p2 = self._project("p2")
        for root in (self.p1, self.p2):
            sd = _sessions_dir(root)
            _make_session(sd, "old", days_ago=100)
            _make_session(sd, "recent", days_ago=1)
            _make_raw_results(root, "old")

    def tearDown(self):
        self._registry_mod.DEFAULT_REGISTRY_PATH = self._orig
        super().tearDown()

    def _project(self, name: str, with_sessions: bool = True) -> Path:
        root = self.tmp / name
        root.mkdir()
        (root / "project.yaml").write_text("name: x\nentrypoints: {}\n", encoding="utf-8")
        if with_sessions:
            _sessions_dir(root).mkdir(parents=True)
        self.registry.register(name, root, validate=False)
        return root

    def _post(self, **body):
        body.setdefault("keep_recent_count", 0)
        return self.client.post("/v1/sessions/cleanup", json=body)

    def test_single_project_dry_run_then_execute(self):
        r = self._post(project="p1", dry_run=True)
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertEqual(data["project"], "p1")
        self.assertEqual([i["session_id"] for i in data["deleted"]], ["old"])
        self.assertGreater(data["raw_results_total_bytes"], 0)
        self.assertTrue((_sessions_dir(self.p1) / "old").exists(), "dry-run 不能真删")

        r = self._post(project="p1", dry_run=False)
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse((_sessions_dir(self.p1) / "old").exists())
        self.assertFalse((self.p1 / ".agent" / "raw_results" / "old").exists())
        # p2 原样保留
        self.assertTrue((_sessions_dir(self.p2) / "old").exists())
        self.assertTrue((self.p2 / ".agent" / "raw_results" / "old").exists())

    def test_unregistered_project_is_404(self):
        self.assertEqual(self._post(project="nope").status_code, 404)

    def test_path_like_project_name_is_404_not_traversal(self):
        for name in ("../p1", str(self.p1), "p1/.."):
            self.assertEqual(self._post(project=name, dry_run=False).status_code, 404, name)
        self.assertTrue((_sessions_dir(self.p1) / "old").exists())

    def test_project_and_all_are_mutually_exclusive(self):
        r = self._post(project="p1", all_external_projects=True)
        self.assertEqual(r.status_code, 400)

    def test_empty_project_name_is_400(self):
        self.assertEqual(self._post(project="  ").status_code, 400)

    def test_project_without_sessions_dir_is_empty_ok(self):
        self._project("fresh", with_sessions=False)
        r = self._post(project="fresh")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["deleted"], [])
        self.assertFalse(_sessions_dir(self.tmp / "fresh").exists())

    def test_project_root_missing_is_404(self):
        import shutil
        root = self._project("gone")
        shutil.rmtree(root)
        self.assertEqual(self._post(project="gone").status_code, 404)

    def test_all_external_projects_grouped_and_tolerates_broken_ones(self):
        import shutil
        self._project("fresh", with_sessions=False)
        gone = self._project("gone")
        shutil.rmtree(gone)

        r = self._post(all_external_projects=True, dry_run=False)
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        by_name = {p["project"]: p for p in data["projects"]}
        self.assertEqual(set(by_name), {"p1", "p2", "fresh", "gone"})
        self.assertEqual([i["session_id"] for i in by_name["p1"]["deleted"]], ["old"])
        self.assertEqual([i["session_id"] for i in by_name["p2"]["deleted"]], ["old"])
        self.assertTrue(by_name["gone"]["error"])
        self.assertFalse(by_name["fresh"]["error"])
        self.assertEqual(data["deleted"], [], "全部模式下顶层不放明细")
        self.assertEqual(data["total_scanned"], 4)
        for root in (self.p1, self.p2):
            self.assertFalse((_sessions_dir(root) / "old").exists())
            self.assertFalse((root / ".agent" / "raw_results" / "old").exists())
            self.assertTrue((_sessions_dir(root) / "recent").exists())

    def test_extract_first_is_ignored_for_external(self):
        # 外部项目里"大且未抽取"的会话：即使传 extract_first=True 也不能被删
        _make_session(_sessions_dir(self.p1), "big", days_ago=100, turns=10)
        r = self._post(project="p1", dry_run=False, extract_first=True)
        self.assertEqual(r.status_code, 200, r.text)
        data = r.json()
        self.assertIn("不支持先补抽取", data["summary"])
        self.assertTrue((_sessions_dir(self.p1) / "big").exists())
        self.assertEqual([i["session_id"] for i in data["skipped_pending_extraction"]], ["big"])

    def test_scopes_endpoint_lists_registered_projects(self):
        self._project("fresh", with_sessions=False)
        r = self.client.get("/v1/sessions/cleanup/scopes")
        self.assertEqual(r.status_code, 200, r.text)
        by_name = {p["name"]: p for p in r.json()["projects"]}
        self.assertEqual(set(by_name), {"p1", "p2", "fresh"})
        self.assertTrue(by_name["p1"]["has_sessions_dir"])
        self.assertFalse(by_name["fresh"]["has_sessions_dir"])

    def test_old_request_shape_still_validates(self):
        from mini_agent.api.models import SessionCleanupRequest
        req = SessionCleanupRequest(dry_run=True)
        self.assertIsNone(req.project)
        self.assertFalse(req.all_external_projects)


# ── 5. agent 自主通道不得触发外部项目范围 ─────────────────────────────────

class TestSlashCommandGuard(unittest.TestCase):
    def test_external_scope_denied_for_agent(self):
        from mini_agent.tools.slash_command import _is_denied
        self.assertTrue(_is_denied("session", "session cleanup --project p1"))
        self.assertTrue(_is_denied("session", "session cleanup --all-external-projects --dry-run"))

    def test_plain_cleanup_still_allowed_for_cron(self):
        from mini_agent.tools.slash_command import _is_denied
        self.assertFalse(_is_denied("session", "session cleanup --extract-first"))
        self.assertFalse(_is_denied("session", "session cleanup --dry-run"))


if __name__ == "__main__":
    unittest.main()
