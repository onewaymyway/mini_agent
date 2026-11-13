"""“打开”链接：直接在当前设备的新标签页中打开原文（其他设备访问看板时使用，不经过服务端浏览器）。"""
import os
import unittest
from pathlib import Path

from streamlit import cache_data
from streamlit.testing.v1 import AppTest

from core.settings import load_settings
from tests.helpers import make_project
from tests.test_feed import _item, _snap, _src
from tests.test_feed_p2 import _distinct_titles
from datetime import datetime, timedelta

APP = str(Path(__file__).resolve().parents[1] / "app.py")


class OpenLinkTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"a": _src("a", "源A")})
        settings = load_settings(self.root)
        self.titles = _distinct_titles(2, seed=5)
        _snap(settings, "a", datetime.now().astimezone() - timedelta(minutes=5),
              [_item("a", t) for t in self.titles])
        os.environ["INFO_HUB_ROOT"] = str(self.root)
        os.environ["INFO_HUB_BACKGROUND_TICK"] = "0"
        cache_data.clear()

    def test_feed_cards_have_new_tab_link_and_dedicated_button(self):
        at = AppTest.from_file(APP, default_timeout=60)
        at.run()
        self.assertFalse(at.exception, [e.value for e in at.exception])
        # 每张卡片的“打开”链接：指向原文地址，且是普通链接元素
        links = [el for el in at.get("link_button")]
        self.assertTrue(links, "应当渲染“打开”链接")
        for el in links:
            self.assertEqual(el.proto.label, "打开")
            self.assertTrue(el.proto.url.startswith("https://"))
        # 原有的“🖥 打开”按钮仍然保留
        dedicated = [b for b in at.button if b.label == "🖥 打开"]
        self.assertTrue(dedicated)
        # 每个专用浏览器按钮旁边都有一个“打开”链接（链接数不少于按钮数）
        self.assertGreaterEqual(len(links), len(dedicated))


if __name__ == "__main__":
    unittest.main()
