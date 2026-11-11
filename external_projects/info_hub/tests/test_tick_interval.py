"""后台 tick 间隔：看板可改，写入运行时覆盖，settings 每次读取最新值，非法值拒绝。"""
import unittest

from core.settings import load_settings
from server import api
from tests.helpers import make_project


class TickIntervalTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        self.settings = load_settings(self.root)

    def test_default_from_settings_yaml(self):
        self.assertEqual(self.settings.tick_interval_minutes, int(self.settings.defaults.get("tick_interval_minutes", 5)))

    def test_set_takes_effect_without_reloading_settings(self):
        api.set_tick_interval(self.settings, 12)
        self.assertEqual(self.settings.tick_interval_minutes, 12)
        self.assertEqual(api.tick_status(self.settings)["tick_interval_minutes"], 12)

    def test_accepts_numeric_string(self):
        self.assertEqual(api.set_tick_interval(self.settings, "15")["tick_interval_minutes"], 15)

    def test_rejects_out_of_range_and_non_numeric(self):
        for bad in (0, -1, 1441, "abc", None, 2.5):
            with self.subTest(bad=bad), self.assertRaises(api.ApiError):
                api.set_tick_interval(self.settings, bad)
        self.assertEqual(api.set_tick_interval(self.settings, 1)["tick_interval_minutes"], 1)
        self.assertEqual(api.set_tick_interval(self.settings, 1440)["tick_interval_minutes"], 1440)

    def test_corrupt_override_falls_back_to_default(self):
        path = self.settings.paths.state_dir / "runtime_config.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"tick_interval_minutes": "bad"}', encoding="utf-8")
        self.assertEqual(self.settings.tick_interval_minutes, int(self.settings.defaults.get("tick_interval_minutes", 5)))


if __name__ == "__main__":
    unittest.main()
