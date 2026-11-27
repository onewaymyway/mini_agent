"""阶段 P19-1：探索参数的统一来源与看板覆盖层（不访问网络）。"""
import json
import unittest

from core import autoexplore, explore_params, review
from core.settings import load_settings
from server import api
from tests.helpers import make_project


class Base(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        self.settings = load_settings(self.root)
        self.runtime = self.settings.paths.state_dir / "runtime_config.json"

    def write_yaml(self, text):
        (self.root / "config").mkdir(exist_ok=True)
        (self.root / "config" / "discovery.yaml").write_text(text, encoding="utf-8")

    def runtime_data(self):
        return json.loads(self.runtime.read_text(encoding="utf-8")) if self.runtime.exists() else {}


class DefaultsAndPriorityTests(Base):
    def test_registry_defaults_match_autoexplore_defaults(self):
        # 注册表是默认值的唯一登记处；autoexplore.DEFAULTS 中每个键都必须有对应登记。
        reg = explore_params.defaults("autoexplore")
        for key, default in autoexplore.DEFAULTS.items():
            self.assertIn(key, reg, key)
            self.assertEqual(reg[key], default, key)

    def test_no_files_means_defaults(self):
        cfg = autoexplore.load_config(self.root)
        self.assertEqual(cfg["daily"], 20)
        self.assertEqual(cfg["render"], "off")
        self.assertFalse(review.load_config(self.root)["enabled"])

    def test_yaml_is_baseline(self):
        self.write_yaml("autoexplore:\n  daily: 7\nreview:\n  every_days: 14\n")
        self.assertEqual(autoexplore.load_config(self.root)["daily"], 7)
        self.assertEqual(review.load_config(self.root)["every_days"], 14)
        rows = {r["key"]: r for r in explore_params.describe(self.root, "autoexplore")["rows"]}
        self.assertEqual(rows["daily"]["source"], "yaml")
        self.assertEqual(rows["daily"]["value"], 7)

    def test_dashboard_override_beats_yaml_and_reset_returns_it(self):
        self.write_yaml("autoexplore:\n  daily: 7\n")
        explore_params.save(self.root, "autoexplore", {"daily": 33})
        self.assertEqual(autoexplore.load_config(self.root)["daily"], 33)
        rows = {r["key"]: r for r in explore_params.describe(self.root, "autoexplore")["rows"]}
        self.assertEqual(rows["daily"]["source"], "dashboard")
        explore_params.reset(self.root, "autoexplore", "daily")
        self.assertEqual(autoexplore.load_config(self.root)["daily"], 7)

    def test_reset_whole_section_falls_back_to_defaults(self):
        explore_params.save(self.root, "review", {"enabled": True, "every_days": 5})
        self.assertTrue(review.load_config(self.root)["enabled"])
        explore_params.reset(self.root, "review")
        self.assertFalse(review.load_config(self.root)["enabled"])
        self.assertEqual(review.load_config(self.root)["every_days"], 30)


class ValidationTests(Base):
    def test_out_of_range_rejected_and_nothing_written(self):
        explore_params.save(self.root, "autoexplore", {"daily": 9})
        before = self.runtime.read_text(encoding="utf-8")
        with self.assertRaises(ValueError):
            explore_params.save(self.root, "autoexplore", {"daily": 9999, "min_items": 2})
        self.assertEqual(self.runtime.read_text(encoding="utf-8"), before)

    def test_unknown_key_and_section_rejected(self):
        with self.assertRaises(ValueError):
            explore_params.save(self.root, "autoexplore", {"no_such_key": 1})
        with self.assertRaises(ValueError):
            explore_params.save(self.root, "nope", {"daily": 1})
        with self.assertRaises(ValueError):
            explore_params.save(self.root, "autoexplore", {})

    def test_type_checks(self):
        spec = {s["key"]: s for s in explore_params.SCHEMA["autoexplore"]}
        with self.assertRaises(ValueError):
            explore_params.coerce(spec["daily"], True)        # 布尔不是整数
        with self.assertRaises(ValueError):
            explore_params.coerce(spec["daily"], 3.5)
        with self.assertRaises(ValueError):
            explore_params.coerce(spec["min_completeness"], float("nan"))
        with self.assertRaises(ValueError):
            explore_params.coerce(spec["render"], "chrome")   # 不在选项中
        self.assertEqual(explore_params.coerce(spec["daily"], "12"), 12)
        self.assertTrue(explore_params.coerce(spec["background"], "on"))
        self.assertFalse(explore_params.coerce(spec["cross_site"], "false"))

    def test_window_format(self):
        spec = {s["key"]: s for s in explore_params.SCHEMA["autoexplore"]}["background_window"]
        self.assertEqual(explore_params.coerce(spec, "22:00-06:00"), "22:00-06:00")
        with self.assertRaises(ValueError):
            explore_params.coerce(spec, "22-06")
        with self.assertRaises(ValueError):
            explore_params.coerce(spec, "25:00-06:00")
        with self.assertRaises(ValueError):
            explore_params.coerce(spec, "00:30-24:10")

    def test_corrupt_override_is_ignored_not_fatal(self):
        self.runtime.parent.mkdir(parents=True, exist_ok=True)
        self.runtime.write_text(json.dumps({"explore_params": {"autoexplore": {"daily": "abc", "min_items": 4}}}),
                                encoding="utf-8")
        cfg = autoexplore.load_config(self.root)
        self.assertEqual(cfg["daily"], 20)          # 坏值回到默认
        self.assertEqual(cfg["min_items"], 4)       # 好值仍生效


class CoexistenceTests(Base):
    def test_save_keeps_other_runtime_settings(self):
        self.runtime.parent.mkdir(parents=True, exist_ok=True)
        self.runtime.write_text(json.dumps({"tick_interval_minutes": 9,
                                            "reports": {"x": {"every": "1h"}}}), encoding="utf-8")
        explore_params.save(self.root, "autoexplore", {"daily": 5})
        explore_params.save(self.root, "review", {"enabled": True})
        data = self.runtime_data()
        self.assertEqual(data["tick_interval_minutes"], 9)
        self.assertEqual(data["reports"], {"x": {"every": "1h"}})
        self.assertEqual(data["explore_params"]["autoexplore"]["daily"], 5)
        self.assertTrue(data["explore_params"]["review"]["enabled"])

    def test_sections_do_not_leak(self):
        explore_params.save(self.root, "autoexplore", {"daily": 5})
        self.assertEqual(review.load_config(self.root)["daily"], 10)


class ApiTests(Base):
    def test_overview_and_save_roundtrip(self):
        ov = api.explore_params_overview(self.settings)
        self.assertEqual({s["section"] for s in ov["sections"]}, {"autoexplore", "review"})
        api.explore_params_save(self.settings, {"section": "autoexplore", "values": {"render": "headed"}})
        self.assertEqual(autoexplore.load_config(self.root)["render"], "headed")
        api.explore_params_reset(self.settings, {"section": "autoexplore", "key": "render"})
        self.assertEqual(autoexplore.load_config(self.root)["render"], "off")

    def test_api_errors_are_400(self):
        with self.assertRaises(api.ApiError) as ctx:
            api.explore_params_save(self.settings, {"section": "autoexplore", "values": {"daily": -1}})
        self.assertEqual(ctx.exception.status, 400)
        with self.assertRaises(api.ApiError):
            api.explore_params_save(self.settings, {"section": "tick", "values": {"x": 1}})
        with self.assertRaises(api.ApiError):
            api.explore_params_reset(self.settings, {"section": "autoexplore", "key": "zzz"})


if __name__ == "__main__":
    unittest.main()
