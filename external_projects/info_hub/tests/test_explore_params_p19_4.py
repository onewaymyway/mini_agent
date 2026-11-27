"""阶段 P19-4：参数变更记录、一键恢复全部默认（不访问网络）。"""
import os
import unittest

from streamlit.testing.v1 import AppTest

from core import autoexplore, explore_params
from core.settings import load_settings
from server import api
from tests.helpers import make_project

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")


class Base(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        self.settings = load_settings(self.root)
        (self.root / "config" / "discovery.yaml").write_text("autoexplore:\n  min_items: 6\n", encoding="utf-8")


class HistoryTests(Base):
    def test_save_records_only_changed_keys_with_old_and_new(self):
        explore_params.save(self.root, "autoexplore", {"daily": 7, "min_items": 6})  # min_items 与 yaml 相同
        rows = explore_params.history(self.root, "autoexplore")
        self.assertEqual([(r["key"], r["old"], r["new"], r["action"]) for r in rows],
                         [("daily", 20, 7, "save")])

    def test_unchanged_save_writes_no_history(self):
        explore_params.save(self.root, "autoexplore", {"min_items": 6})
        self.assertEqual(explore_params.history(self.root), [])

    def test_reset_records_old_dashboard_value_and_new_effective_value(self):
        explore_params.save(self.root, "autoexplore", {"min_items": 9})
        explore_params.reset(self.root, "autoexplore", "min_items")
        latest = explore_params.history(self.root, "autoexplore", 1)[0]
        self.assertEqual((latest["key"], latest["action"], latest["old"], latest["new"]),
                         ("min_items", "reset", 9, 6))             # 回到 yaml 的 6，而不是默认 3

    def test_history_newest_first_limit_and_section_filter(self):
        explore_params.save(self.root, "autoexplore", {"daily": 1})
        explore_params.save(self.root, "review", {"enabled": True})
        explore_params.save(self.root, "autoexplore", {"daily": 2})
        all_rows = explore_params.history(self.root)
        self.assertEqual([r["new"] for r in all_rows], [2, True, 1])
        self.assertEqual(len(explore_params.history(self.root, limit=2)), 2)
        self.assertEqual([r["section"] for r in explore_params.history(self.root, "review")], ["review"])

    def test_unknown_section_rejected(self):
        with self.assertRaises(ValueError):
            explore_params.history(self.root, "nope")


class ResetAllTests(Base):
    def test_reset_all_returns_every_section_to_yaml_or_default(self):
        explore_params.save(self.root, "autoexplore", {"min_items": 9, "daily": 1})
        explore_params.save(self.root, "review", {"enabled": True})
        explore_params.save(self.root, "discovery_limits", {"daily": 8})
        explore_params.reset_all(self.root)
        self.assertEqual(autoexplore.load_config(self.root)["min_items"], 6)     # yaml 基线保留
        self.assertEqual(autoexplore.load_config(self.root)["daily"], 20)       # 默认
        self.assertFalse(explore_params.overrides(self.root))
        yaml_text = (self.root / "config" / "discovery.yaml").read_text(encoding="utf-8")
        self.assertIn("min_items: 6", yaml_text)                                 # yaml 未被改动

    def test_reset_all_records_history_for_each_changed_key(self):
        explore_params.save(self.root, "review", {"enabled": True})
        explore_params.reset_all(self.root)
        rows = explore_params.history(self.root, "review")          # 最新在前：先是恢复，再是之前的保存
        self.assertEqual([(r["key"], r["action"], r["new"]) for r in rows],
                         [("enabled", "reset", False), ("enabled", "save", True)])


class ApiTests(Base):
    def test_api_reset_all_and_history(self):
        api.explore_params_save(self.settings, {"section": "autoexplore", "values": {"daily": 4}})
        out = api.explore_params_reset(self.settings, {"section": "all"})
        self.assertEqual({s["section"] for s in out["sections"]}, set(api.EXPLORE_PARAM_SECTIONS))
        hist = api.explore_params_history(self.settings, "autoexplore", 10)["rows"]
        self.assertEqual([r["action"] for r in hist], ["reset", "save"])

    def test_reset_all_rejects_single_key_and_bad_history_section(self):
        with self.assertRaises(api.ApiError):
            api.explore_params_reset(self.settings, {"section": "all", "key": "daily"})
        with self.assertRaises(api.ApiError):
            api.explore_params_history(self.settings, "tick")


class DashboardTests(Base):
    def setUp(self):
        super().setUp()
        os.environ["INFO_HUB_ROOT"] = str(self.root)
        os.environ["INFO_HUB_BACKGROUND_TICK"] = "0"

    def tearDown(self):
        os.environ.pop("INFO_HUB_ROOT", None)

    def test_reset_all_button_disabled_until_confirmed_then_clears(self):
        explore_params.save(self.root, "autoexplore", {"daily": 5})
        explore_params.save(self.root, "review", {"enabled": True})
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        reset_btn = [b for b in at.button if b.key == "ep_reset_all"][0]
        self.assertTrue(reset_btn.disabled)
        at.checkbox(key="ep_reset_all_confirm").check()
        at.run()
        [b for b in at.button if b.key == "ep_reset_all"][0].click()
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertEqual(autoexplore.load_config(self.root)["daily"], 20)
        self.assertFalse(explore_params.overrides(self.root))
        self.assertEqual(at.number_input(key="ep_autoexplore_daily").value, 20)  # 表单已刷新为默认值
        self.assertFalse(at.checkbox(key="ep_reset_all_confirm").value)          # 确认框已复位

    def test_history_table_rendered(self):
        explore_params.save(self.root, "autoexplore", {"daily": 5})
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        frames = [d.value for d in at.dataframe]
        self.assertTrue(any("参数" in f.columns and "原值" in f.columns for f in frames if hasattr(f, "columns")))


if __name__ == "__main__":
    unittest.main()
