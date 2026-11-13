"""推荐页“换一批”：排除当前屏幕上的条目，换成未展示过的内容；刷新后重新开始。"""
import os
import re
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from streamlit import cache_data
from streamlit.testing.v1 import AppTest

from core import feed
from core.settings import load_settings
from tests.helpers import make_project
from tests.test_feed import _item, _snap, _src
from tests.test_feed_p2 import _distinct_titles

APP = str(Path(__file__).resolve().parents[1] / "app.py")


class SwapTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"a": _src("a", "源A")})
        self.settings = load_settings(self.root)
        self.titles = _distinct_titles(50, seed=31)
        _snap(self.settings, "a", datetime.now().astimezone() - timedelta(minutes=5),
              [_item("a", t) for t in self.titles])
        os.environ["INFO_HUB_ROOT"] = str(self.root)
        os.environ["INFO_HUB_BACKGROUND_TICK"] = "0"
        cache_data.clear()

    def _bold(self, at):
        """页面上展示的条目标题（只取本测试的标题，排除页面其他粗体文字）。"""
        shown = {m.value[2:-2] for m in at.markdown if re.fullmatch(r"\*\*[^*]+\*\*", m.value or "")}
        return shown & set(self.titles)

    def test_exclude_removes_items_from_build(self):
        from core.settings import load_settings as ls
        now = datetime.now().astimezone()
        srcs = [{"id": "a", "name": "源A", "kind": "feed"}]
        cfg = feed.load_config(self.settings.raw)
        base = feed.build_feed(data_dir=self.settings.paths.data_dir, raw_dir=self.settings.paths.raw_dir,
                               sources=srcs, hours=24, cfg=cfg, now=now)
        keys = [src["item_key"] for c in base["sections"]["featured"] for src in c["sources"]]
        self.assertTrue(keys)
        res = feed.build_feed(data_dir=self.settings.paths.data_dir, raw_dir=self.settings.paths.raw_dir,
                              sources=srcs, hours=24, cfg=cfg, now=now, exclude=set(keys))
        remaining = {src["item_key"] for c in res["sections"]["featured"] for src in c["sources"]}
        self.assertFalse(remaining & set(keys))
        self.assertEqual(res["stats"]["excluded"], len(keys))

    def test_swap_replaces_shown_items_and_refresh_restarts(self):
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        first = self._bold(at)
        self.assertTrue(first)
        swap = [b for b in at.button if b.key == "feed_swap"]
        self.assertEqual(len(swap), 1)
        swap[0].click().run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        second = self._bold(at)
        self.assertTrue(second, "还有未展示过的内容时，换一批应给出新的一批")
        self.assertFalse(first & second, "换一批后不应再出现刚才展示过的条目")
        # 刷新推荐：排除清空，重新从头开始
        [b for b in at.button if b.key == "feed_refresh"][0].click().run()
        self.assertEqual(self._bold(at), first)


if __name__ == "__main__":
    unittest.main()
