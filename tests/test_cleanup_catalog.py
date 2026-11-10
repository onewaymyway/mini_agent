"""tests/test_cleanup_catalog.py — 清理中心（next_doc/cleanup_center_plan.md）。

覆盖：
  1. 类别清单：key 唯一、默认值合法、高风险默认不勾选。
  2. 各类别的年龄判定：过期删除、新近保留、dry-run 不删。
  3. adhoc temp 只清 temp/，output/ 不被触碰。
  4. 全局日志：只删已轮转文件，活动日志只报告。
  5. 参数校验：未知 key、非法天数在进入线程池前拒绝。
  6. HTTP 路由：categories、run 的 dry-run/执行、未知 key → 400、外部项目注册名解析。
"""

from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from fastapi import FastAPI
from fastapi.testclient import TestClient

from mini_agent.api.routes import router
from mini_agent.evolution.cleanup_catalog import (
    CATEGORIES, list_categories, run_categories, validate_selection,
)
from mini_agent.external_projects.registry import ExternalProjectRegistry

OLD_TS = time.time() - 200 * 86400


def _age(path: Path, ts: float = OLD_TS) -> None:
    for p in [path, *path.rglob("*")]:
        try:
            os.utime(p, (ts, ts))
        except OSError:
            pass


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.root = self.tmp / "proj"
        self.root.mkdir()
        self.home = self.tmp / "home"
        self.home.mkdir()
        # 全局日志目录 ~/.agent/logs 指向临时 home，避免触碰真实用户目录
        self._home_patch = mock.patch("pathlib.Path.home", return_value=self.home)
        self._home_patch.start()

    def tearDown(self):
        self._home_patch.stop()
        self._tmp.cleanup()

    def _agent(self, *parts) -> Path:
        return self.root.joinpath(".agent", *parts)


class CatalogTests(_Base):
    def test_keys_unique_and_high_risk_unchecked(self):
        keys = [c.key for c in CATEGORIES]
        self.assertEqual(len(keys), len(set(keys)))
        by_key = {c.key: c for c in CATEGORIES}
        self.assertEqual(by_key["daemon_run_outputs"].risk, "high")
        self.assertFalse(by_key["daemon_run_outputs"].default_checked)
        self.assertTrue(by_key["cache"].default_checked)
        self.assertEqual(len(list_categories()), len(CATEGORIES))

    def test_external_input_is_not_a_category(self):
        self.assertNotIn("external_input", {c.key for c in CATEGORIES})


class AgeRuleTests(_Base):
    def test_old_cache_entry_deleted_fresh_kept(self):
        cache = self._agent("cache")
        cache.mkdir(parents=True)
        old = cache / "old.json"
        old.write_text("{}", encoding="utf-8")
        _age(old)
        fresh = cache / "fresh.json"
        fresh.write_text("{}", encoding="utf-8")

        res = run_categories(self.root, ["cache"], dry_run=False)[0]
        self.assertEqual({i.name for i in res.deleted}, {"old.json"})
        self.assertFalse(old.exists())
        self.assertTrue(fresh.exists())

    def test_dry_run_deletes_nothing(self):
        debug = self._agent("debug")
        debug.mkdir(parents=True)
        old = debug / "trace.txt"
        old.write_text("x", encoding="utf-8")
        _age(old)
        res = run_categories(self.root, ["debug"], dry_run=True)[0]
        self.assertEqual(len(res.to_delete), 1)
        self.assertTrue(old.exists())

    def test_age_override_applies(self):
        rep = self._agent("daily_reports")
        rep.mkdir(parents=True)
        f = rep / "2026-01-01.md"
        f.write_text("r", encoding="utf-8")
        _age(f, time.time() - 10 * 86400)  # 10 天前
        # 默认 90 天不删
        self.assertEqual(run_categories(self.root, ["daily_reports"], dry_run=True)[0].to_delete, [])
        # 改为 5 天则删
        res = run_categories(self.root, ["daily_reports"], age_days={"daily_reports": 5}, dry_run=True)[0]
        self.assertEqual(len(res.to_delete), 1)

    def test_daemon_run_outputs_scans_goals_and_cron(self):
        base = self._agent("daemon_run_outputs")
        (base / "goals").mkdir(parents=True)
        (base / "cron").mkdir(parents=True)
        g = base / "goals" / "g1"
        g.mkdir()
        (g / "out.md").write_text("x", encoding="utf-8")
        _age(g)
        res = run_categories(self.root, ["daemon_run_outputs"], dry_run=True)[0]
        self.assertEqual([i.name for i in res.to_delete], ["goals/g1"])


class AdhocTests(_Base):
    def test_only_temp_is_touched_output_is_kept(self):
        adhoc = self._agent("adhoc", "unbound")
        stale = adhoc / "temp" / "stale"
        stale.mkdir(parents=True)
        (stale / "a.txt").write_text("x", encoding="utf-8")
        _age(stale)
        out = adhoc / "output" / "deliverable.md"
        out.parent.mkdir(parents=True)
        out.write_text("keep", encoding="utf-8")
        _age(out.parent)

        res = run_categories(self.root, ["adhoc_temp"], dry_run=False)[0]
        self.assertEqual([i.name for i in res.deleted], ["unbound/temp/stale"])
        self.assertFalse(stale.exists())
        self.assertTrue(out.exists())


class RawOrphanTests(_Base):
    def test_unreferenced_raw_deleted_referenced_kept(self):
        ghost = self._agent("raw_results", "ghost")
        ghost.mkdir(parents=True)
        (ghost / "x.txt").write_text("x", encoding="utf-8")
        _age(ghost)
        live = self._agent("raw_results", "live_session")
        live.mkdir(parents=True)
        (live / "x.txt").write_text("x", encoding="utf-8")
        _age(live)
        (self._agent("sessions") / "live_session").mkdir(parents=True)

        res = run_categories(self.root, ["raw_orphan"], dry_run=False)[0]
        self.assertEqual([i.name for i in res.deleted], ["ghost"])
        self.assertFalse(ghost.exists())
        self.assertTrue(live.exists())


class GlobalLogTests(_Base):
    def test_rotated_deleted_active_only_reported(self):
        logs = self.home / ".agent" / "logs"
        logs.mkdir(parents=True)
        active = logs / "error.jsonl"
        active.write_text("{}\n" * 5, encoding="utf-8")
        rotated = logs / "error.jsonl.1"
        rotated.write_text("{}\n", encoding="utf-8")
        _age(rotated)
        gz = logs / "http_access.jsonl.2.gz"
        gz.write_bytes(b"x")
        _age(gz)

        res = run_categories(self.root, ["global_rotated_logs"], dry_run=False)[0]
        deleted = {i.name for i in res.deleted}
        self.assertEqual(deleted, {"error.jsonl.1", "http_access.jsonl.2.gz"})
        self.assertTrue(active.exists())
        active_items = [i for i in res.items if i.name == "error.jsonl"]
        self.assertEqual(active_items[0].action, "keep")


class ValidationTests(_Base):
    def test_unknown_key_rejected(self):
        with self.assertRaises(ValueError):
            validate_selection(["cache", "external_input"], None)
        with self.assertRaises(ValueError):
            run_categories(self.root, ["nope"])

    def test_bad_age_rejected(self):
        for bad in (-1, 99999, float("nan"), "abc"):
            with self.assertRaises(ValueError):
                validate_selection(["cache"], {"cache": bad})

    def test_unknown_key_in_age_map_rejected(self):
        with self.assertRaises(ValueError):
            validate_selection(["cache"], {"external_input": 3})


class RouteTests(_Base):
    def setUp(self):
        super().setUp()
        self.reg_path = self.tmp / "registry.json"
        import mini_agent.external_projects.registry as registry_mod
        self._registry_mod = registry_mod
        self._orig = registry_mod.DEFAULT_REGISTRY_PATH
        registry_mod.DEFAULT_REGISTRY_PATH = self.reg_path
        app = FastAPI()
        app.include_router(router)
        app.state.http_server = None
        app.state.project_root = self.root
        self.client = TestClient(app)

    def tearDown(self):
        self._registry_mod.DEFAULT_REGISTRY_PATH = self._orig
        super().tearDown()

    def test_categories_endpoint(self):
        r = self.client.get("/v1/cleanup/categories")
        self.assertEqual(r.status_code, 200)
        keys = {c["key"] for c in r.json()["categories"]}
        self.assertIn("cache", keys)
        self.assertNotIn("external_input", keys)

    def test_run_dry_run_then_execute(self):
        cache = self._agent("cache")
        cache.mkdir(parents=True)
        f = cache / "old.json"
        f.write_text("{}", encoding="utf-8")
        _age(f)
        r = self.client.post("/v1/cleanup/run", json={"keys": ["cache"], "dry_run": True})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["dry_run"])
        self.assertEqual(body["results"][0]["delete_count"], 1)
        self.assertTrue(f.exists())

        r = self.client.post("/v1/cleanup/run", json={"keys": ["cache"], "dry_run": False})
        self.assertEqual(r.json()["results"][0]["deleted_count"], 1)
        self.assertFalse(f.exists())

    def test_unknown_key_is_400(self):
        r = self.client.post("/v1/cleanup/run", json={"keys": ["external_input"]})
        self.assertEqual(r.status_code, 400)

    def test_empty_keys_is_400(self):
        r = self.client.post("/v1/cleanup/run", json={"keys": []})
        self.assertEqual(r.status_code, 400)

    def test_unregistered_external_project_is_404(self):
        r = self.client.post("/v1/cleanup/run", json={"keys": ["cache"], "project": "nope"})
        self.assertEqual(r.status_code, 404)

    def test_external_project_scope_uses_its_own_root(self):
        ext = self.tmp / "ext"
        ext.mkdir()
        ExternalProjectRegistry(self.reg_path).register(name="ext1", path=ext, validate=False)
        c = ext / ".agent" / "cache"
        c.mkdir(parents=True)
        f = c / "old.json"
        f.write_text("{}", encoding="utf-8")
        _age(f)
        r = self.client.post("/v1/cleanup/run",
                             json={"keys": ["cache"], "dry_run": False, "project": "ext1"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["project"], "ext1")
        self.assertFalse(f.exists())


if __name__ == "__main__":
    unittest.main()
