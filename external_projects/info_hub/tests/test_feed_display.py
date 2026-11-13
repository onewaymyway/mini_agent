"""推荐页展示数量：默认值提高、可在看板调整、保存后生效、非法值被限制。"""
import os
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from streamlit import cache_data
from streamlit.testing.v1 import AppTest

from core import feed
from core.settings import load_settings
from server import api
from tests.helpers import make_project
from tests.test_feed import _item, _snap, _src
from tests.test_feed_p2 import _distinct_titles

APP = str(Path(__file__).resolve().parents[1] / "app.py")


class FeedDisplayTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"a": _src("a", "源A")})
        self.settings = load_settings(self.root)
        self.titles = _distinct_titles(60, seed=41)
        _snap(self.settings, "a", datetime.now().astimezone() - timedelta(minutes=5),
              [_item("a", t) for t in self.titles])
        os.environ["INFO_HUB_ROOT"] = str(self.root)
        os.environ["INFO_HUB_BACKGROUND_TICK"] = "0"
        cache_data.clear()

    def test_defaults_are_larger(self):
        self.assertGreater(feed.DEFAULT_CONFIG["others_limit"], 30)
        self.assertGreater(feed.DEFAULT_CONFIG["featured_size"], 10)

    def test_save_and_reset_and_clamp(self):
        with self.assertRaises(api.ApiError):
            api.feed_display_set_api(self.settings, {"values": {"bogus": 5}})
        out = api.feed_display_set_api(self.settings, {"values": {"featured_size": 999, "others_limit": 0}})
        self.assertEqual(out["values"]["featured_size"], feed.DISPLAY_LIMITS["featured_size"][1])
        self.assertEqual(out["values"]["others_limit"], 1)
        self.assertEqual(sorted(out["overridden"]), ["featured_size", "others_limit"])
        out = api.feed_display_set_api(self.settings, {"reset": True})
        self.assertEqual(out["values"]["featured_size"], feed.DEFAULT_CONFIG["featured_size"])
        self.assertEqual(out["overridden"], [])

    def test_explore_size_is_settable_and_zero_disables(self):
        out = api.feed_display_set_api(self.settings, {"values": {"explore_size": 9}})
        self.assertEqual(out["values"]["explore_size"], 9)
        self.assertIn("explore_size", out["overridden"])
        res = api.feed_view(self.settings, hours=24)
        self.assertLessEqual(len(res["sections"]["explore"]), 9)
        api.feed_display_set_api(self.settings, {"values": {"explore_size": 0}})
        res = api.feed_view(self.settings, hours=24)
        self.assertEqual(res["sections"]["explore"], [])

    def test_build_uses_saved_limits(self):
        api.feed_display_set_api(self.settings, {"values": {"others_limit": 12}})
        res = api.feed_view(self.settings, hours=24)
        self.assertLessEqual(len([i for i in res["impressions"] if i["section"] == "others"]), 12)
        api.feed_display_set_api(self.settings, {"values": {"featured_size": 3}})
        res = api.feed_view(self.settings, hours=24)
        self.assertLessEqual(len(res["sections"]["featured"]), 3)

    def test_panel_saves_value(self):
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        inp = [n for n in at.number_input if n.key == "disp_featured_size"]
        self.assertEqual(len(inp), 1)
        inp[0].set_value(4)
        [b for b in at.button if b.key == "disp_save"][0].click().run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertEqual(api.feed_display_get_api(self.settings)["values"]["featured_size"], 4)


if __name__ == "__main__":
    unittest.main()
