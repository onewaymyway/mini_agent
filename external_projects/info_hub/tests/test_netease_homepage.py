"""网易新闻首页（html_list）：只取文章链接，忽略频道导航，标题与链接正确。"""
import unittest
from pathlib import Path

from adapters.html_list import parse_list
from core.settings import load_settings
from core.sources import load_sources

ROOT = Path(__file__).resolve().parent.parent

HTML = """
<html><body>
<nav><a href="https://c.m.163.com/news/sub/T1348647909107.html">头条</a>
<a href="https://c.m.163.com/news/hot/newsList">热门</a></nav>
<h2>今日要闻</h2>
<ul>
  <li><a href="https://c.m.163.com/news/a/L9K8JTJS000189FH.html">时政微观察丨山河铭记</a><span>央视新闻客户端2026-10-19</span></li>
  <li><a href="https://c.m.163.com/news/v/VD8M8ARG4.html">外卖小哥坐电动车上靠着护栏</a><span>直击新鲜事2026-10-19</span></li>
  <li><a href="https://c.m.163.com/news/s/S1791966107305.html">三季度GDP，同比增长4.5%</a></li>
  <li><a href="https://c.m.163.com/news/a/L9K22JPL0530NLC9.html">韩国养老金，血亏一万亿</a><span>中国基金报2026-10-19 14:30</span></li>
</ul>
</body></html>
"""


class NeteaseHomepageTests(unittest.TestCase):
    def test_spec_loads_and_is_html_list(self):
        res = load_sources(load_settings(ROOT))
        self.assertEqual(res.errors, {})
        spec = res.specs["netease_news"]
        self.assertTrue(spec.enabled)
        self.assertEqual(spec.adapter, "html_list")

    def test_only_article_links_are_extracted(self):
        spec = load_sources(load_settings(ROOT)).specs["netease_news"]
        items = parse_list(HTML, spec.parse["list_selector"], spec.parse["fields"], 100)
        self.assertEqual([i.title for i in items], [
            "时政微观察丨山河铭记",
            "外卖小哥坐电动车上靠着护栏",
            "三季度GDP，同比增长4.5%",
            "韩国养老金，血亏一万亿",
        ])
        self.assertEqual(items[0].url, "https://c.m.163.com/news/a/L9K8JTJS000189FH.html")
        self.assertTrue(all("/news/sub/" not in i.url and "/news/hot/" not in i.url for i in items))

    def test_published_at_extracted_with_and_without_time(self):
        spec = load_sources(load_settings(ROOT)).specs["netease_news"]
        items = parse_list(HTML, spec.parse["list_selector"], spec.parse["fields"], 100)
        self.assertEqual(items[0].published_at, "2026-10-19")
        self.assertEqual(items[1].published_at, "2026-10-19")
        self.assertIsNone(items[2].published_at)  # 无日期的条目保持 None
        self.assertEqual(items[3].published_at, "2026-10-19 14:30")


if __name__ == "__main__":
    unittest.main()
