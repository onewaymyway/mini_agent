"""小红书发现页选择器：基于探测到的结构，验证标题、链接、作者、点赞数提取。"""
import unittest
from pathlib import Path

from adapters.html_list import parse_list
from core.settings import load_settings
from core.sources import load_sources

ROOT = Path(__file__).resolve().parent.parent

HTML = """
<div class="feeds">
<section class="note-item">
  <div class="cover-wrapper"><a class="cover mask" href="/explore/65a1b2c3"><img src="x.jpg"></a></div>
  <div class="footer">
    <a class="title" href="/explore/65a1b2c3">今天的穿搭分享</a>
    <div class="author-wrapper"><a class="author"><span class="name">小明同学</span></a>
      <span class="like-wrapper"><span class="count">1.2万</span></span></div>
  </div>
</section>
<section class="note-item">
  <div class="cover-wrapper"><a class="cover mask" href="/explore/65a1b2c4"><img src="y.jpg"></a></div>
  <div class="footer">
    <a class="title" href="/explore/65a1b2c4">周末去哪里玩</a>
    <div class="author-wrapper"><a class="author"><span class="name">旅行达人</span></a>
      <span class="like-wrapper"><span class="count">388</span></span></div>
  </div>
</section>
</div>
"""


class XhsFeedTests(unittest.TestCase):
    def test_selectors_extract_title_url_author_likes(self):
        spec = load_sources(load_settings(ROOT)).specs["xiaohongshu_hot"]
        self.assertTrue(spec.enabled)
        items = parse_list(HTML, spec.parse["list_selector"], spec.parse["fields"], 40)
        self.assertEqual([i.title for i in items], ["今天的穿搭分享", "周末去哪里玩"])
        self.assertTrue(items[0].url.endswith("/explore/65a1b2c3"))  # 绝对地址在 fetch 中补全
        self.assertEqual(items[0].heat, "1.2万")
        self.assertEqual(items[0].summary, "小明同学")


if __name__ == "__main__":
    unittest.main()
