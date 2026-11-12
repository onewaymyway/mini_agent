"""推荐页交互：反馈不改变列表，只有“刷新推荐”或重新加载才更新（会话快照）。"""
import os
import re
import unittest
from datetime import datetime, timedelta

from streamlit import cache_data
from streamlit.testing.v1 import AppTest

from core.feedback import read_feedback
from core.settings import load_settings
from pathlib import Path
from tests.helpers import make_project
from tests.test_feed import _item, _snap, _src
from tests.test_feed_p2 import _distinct_titles

APP = str(Path(__file__).resolve().parents[1] / "app.py")


class FeedSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"a": _src("a", "源A"), "b": _src("b", "源B")})
        self.settings = load_settings(self.root)
        now = datetime.now().astimezone()
        self.titles = _distinct_titles(3, seed=21)
        _snap(self.settings, "a", now - timedelta(minutes=5), [_item("a", t) for t in self.titles])
        os.environ["INFO_HUB_ROOT"] = str(self.root)
        os.environ["INFO_HUB_BACKGROUND_TICK"] = "0"
        cache_data.clear()

    def _bold(self, at):
        return [m.value for m in at.markdown if re.fullmatch(r"\*\*[^*]+\*\*", m.value or "")]

    def test_feedback_keeps_list_until_refresh(self):
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        before = self._bold(at)
        target = f"**{self.titles[0]}**"
        self.assertIn(target, before)

        up = [b for b in at.button if b.key and b.key.startswith("feed_fb_interested_")][0]
        up.click().run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        # 反馈已写入，但列表仍然原样，并在原地显示标记
        self.assertTrue(read_feedback(self.settings.paths.data_dir))
        self.assertEqual(self._bold(at), before)
        self.assertTrue(any("已记录：感兴趣" in c.value for c in at.caption), [c.value for c in at.caption])

        # 手动刷新后，已处理的条目从列表消失
        [b for b in at.button if b.key == "feed_refresh"][0].click().run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertNotIn(target, self._bold(at))

    def test_hide_source_does_not_refresh_list(self):
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        before = self._bold(at)
        [b for b in at.button if b.key and b.key.startswith("feed_hide_")][0].click().run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        self.assertEqual(self._bold(at), before)

    def test_snapshot_not_rebuilt_by_unrelated_reruns(self):
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        snap1 = at.session_state["feed_snap"]["res"]["generated_at"]
        up = [b for b in at.button if b.key and b.key.startswith("feed_fb_interested_")][0]
        up.click().run()
        at.toggle(key="feed_triage").set_value(True).run()
        at.toggle(key="feed_triage").set_value(False).run()
        self.assertEqual(at.session_state["feed_snap"]["res"]["generated_at"], snap1)


if __name__ == "__main__":
    unittest.main()
