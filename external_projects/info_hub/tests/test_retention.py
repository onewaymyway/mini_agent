"""数据保留与清理：过程数据按天数删除，用户反馈与行为记录永久保留，每个信息源保留最新快照。"""
import json
import os
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from streamlit import cache_data
from streamlit.testing.v1 import AppTest

from core import retention
from core.evaluation import exposure_path
from core.feed import shown_path
from core.feedback import feedback_path
from core.settings import load_settings
from core.store import append_jsonl
from server import api
from tests.helpers import make_project

APP = str(Path(__file__).resolve().parents[1] / "app.py")
SRC = {"id": "a", "name": "A", "category": "news", "kind": "feed", "adapter": "rss",
       "request": {"url": "https://a.example/feed"}}


def _lines(path):
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines() if x.strip()]


class RetentionTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"a": SRC})
        self.st = load_settings(self.root)
        self.d = self.st.paths.data_dir
        self.now = datetime.now().astimezone()
        self.old = self.now - timedelta(days=200)
        # 抓取快照：a 有旧快照与新快照；b 只有一份旧快照（必须保留）
        for day in (self.old, self.now):
            p = self.st.paths.raw_dir / "a" / f"{day:%Y-%m-%d}"
            p.mkdir(parents=True, exist_ok=True)
            (p / "120000.json").write_text("{}", encoding="utf-8")
        pb = self.st.paths.raw_dir / "b" / f"{self.old:%Y-%m-%d}"
        pb.mkdir(parents=True, exist_ok=True)
        (pb / "120000.json").write_text("{}", encoding="utf-8")
        self.old_raw = self.st.paths.raw_dir / "a" / f"{self.old:%Y-%m-%d}" / "120000.json"
        self.b_raw = pb / "120000.json"
        # 条目日志与运行记录
        (self.st.paths.items_dir / "a").mkdir(parents=True, exist_ok=True)
        self.old_items = self.st.paths.items_dir / "a" / f"{self.old:%Y-%m-%d}.jsonl"
        self.old_items.write_text("{}\n", encoding="utf-8")
        self.st.paths.runs_dir.mkdir(parents=True, exist_ok=True)
        self.old_run = self.st.paths.runs_dir / f"{self.old:%Y-%m-%d}.log.md"
        self.old_run.write_text("r\n", encoding="utf-8")
        # 用户数据（即使很旧也必须保留）
        append_jsonl(feedback_path(self.d), {"action": "interested", "time": self.old.isoformat(), "item_key": "k"})
        append_jsonl(exposure_path(self.d), {"kind": "open", "time": self.old.isoformat(), "item_key": "k", "source_id": "a"})
        append_jsonl(exposure_path(self.d), {"kind": "dwell", "time": self.old.isoformat(), "item_key": "k", "seconds": 30})
        # 过程数据：旧的展示记录应删除，新的保留
        append_jsonl(exposure_path(self.d), {"kind": "impression", "time": self.old.isoformat(), "item_keys": ["x"]})
        append_jsonl(exposure_path(self.d), {"kind": "impression", "time": self.now.isoformat(), "item_keys": ["y"]})
        append_jsonl(shown_path(self.d), {"kind": "feed_impression", "time": self.old.isoformat(), "items": []})
        append_jsonl(shown_path(self.d), {"kind": "feed_impression", "time": self.now.isoformat(), "items": []})
        os.environ["INFO_HUB_ROOT"] = str(self.root)
        os.environ["INFO_HUB_BACKGROUND_TICK"] = "0"
        cache_data.clear()

    def test_load_retention_enforces_minimums(self):
        cfg = retention.load_retention({"retention": {"raw_days": 5, "shown_days": 10, "enabled": False}})
        self.assertEqual(cfg["raw_days"], retention.MIN_DAYS["raw_days"])
        self.assertEqual(cfg["shown_days"], retention.MIN_DAYS["shown_days"])
        self.assertFalse(cfg["enabled"])
        self.assertEqual(retention.load_retention(None)["raw_days"], 31)

    def test_dry_run_changes_nothing(self):
        rep = retention.run_retention(self.st, self.now, dry_run=True)
        self.assertTrue(rep["dry_run"])
        self.assertGreater(rep["categories"]["raw_snapshots"]["files"], 0)
        self.assertTrue(self.old_raw.exists())
        self.assertEqual(len(_lines(shown_path(self.d))), 2)

    def test_cleanup_removes_process_data_only(self):
        rep = retention.run_retention(self.st, self.now)
        self.assertGreater(rep["total_bytes"], 0)
        # 过程数据被删除
        self.assertFalse(self.old_raw.exists())
        self.assertFalse(self.old_items.exists())
        self.assertFalse(self.old_run.exists())
        self.assertEqual(len(_lines(shown_path(self.d))), 1)
        kinds = [r["kind"] for r in _lines(exposure_path(self.d))]
        self.assertEqual(kinds.count("impression"), 1)  # 旧的 impression 被删，新的保留
        # 用户数据全部保留
        self.assertEqual(len(_lines(feedback_path(self.d))), 1)
        self.assertIn("open", kinds)
        self.assertIn("dwell", kinds)
        # 每个信息源保留最新快照：b 只有旧快照，仍在
        self.assertTrue(self.b_raw.exists())
        # 新快照保留
        self.assertTrue((self.st.paths.raw_dir / "a" / f"{self.now:%Y-%m-%d}" / "120000.json").exists())

    def test_auto_run_once_per_interval_and_respects_switch(self):
        first = retention.maybe_run_retention(self.st, self.now)
        self.assertIsNotNone(first)
        self.assertIsNone(retention.maybe_run_retention(self.st, self.now + timedelta(hours=1)))
        self.assertIsNotNone(retention.maybe_run_retention(self.st, self.now + timedelta(hours=25)))
        root2 = make_project({"a": SRC})
        (root2 / "config" / "settings.yaml").write_text(
            (root2 / "config" / "settings.yaml").read_text(encoding="utf-8") + "\nretention:\n  enabled: false\n",
            encoding="utf-8")
        self.assertIsNone(retention.maybe_run_retention(load_settings(root2), self.now))

    def test_api_status_and_run(self):
        status = api.retention_status_api(self.st)
        self.assertIn("feedback/events.jsonl（反馈事件）", status["protected"])
        self.assertTrue(self.old_raw.exists())  # 预览不删除
        out = api.retention_run_api(self.st, {"dry_run": False})
        self.assertFalse(out["dry_run"])
        self.assertFalse(self.old_raw.exists())

    def test_dashboard_overrides_take_effect_and_reset(self):
        api.retention_set_api(self.st, {"values": {"raw_days": 45, "impressions_days": 1, "run_every_hours": 0}})
        cfg = retention.effective_retention(self.st)
        self.assertEqual(cfg["raw_days"], 45)
        self.assertEqual(cfg["impressions_days"], retention.MIN_DAYS["impressions_days"])  # 低于下限被提高
        self.assertEqual(cfg["run_every_hours"], retention.RUN_EVERY_RANGE[0])
        status = api.retention_status_api(self.st)
        self.assertEqual(sorted(status["overridden"]), ["impressions_days", "raw_days", "run_every_hours"])
        # 设置 raw_days=45 后，200 天前的快照仍应被删除；把保留期调到 365 天则保留
        api.retention_set_api(self.st, {"values": {"raw_days": 365}})
        retention.run_retention(self.st, self.now, dry_run=True)
        self.assertTrue(self.old_raw.exists())
        api.retention_set_api(self.st, {"reset": True})
        self.assertEqual(retention.effective_retention(self.st)["raw_days"], 31)
        self.assertEqual(api.retention_status_api(self.st)["overridden"], [])

    def test_dashboard_can_disable_auto_cleanup(self):
        api.retention_set_api(self.st, {"values": {"enabled": False}})
        self.assertIsNone(retention.maybe_run_retention(self.st, self.now))
        self.assertFalse(retention.effective_retention(self.st)["enabled"])

    def test_unknown_key_rejected(self):
        from server.api import ApiError
        with self.assertRaises(ApiError):
            api.retention_set_api(self.st, {"values": {"nope": 3}})

    def test_panel_saves_parameters(self):
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        inp = [n for n in at.number_input if n.key == "ret_raw_days"]
        self.assertEqual(len(inp), 1)
        inp[0].set_value(60)
        [b for b in at.button if b.key == "ret_save"][0].click().run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertEqual(retention.effective_retention(self.st)["raw_days"], 60)

    def test_settings_panel_renders(self):
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertIn("立即清理", [b.label for b in at.button])


if __name__ == "__main__":
    unittest.main()
