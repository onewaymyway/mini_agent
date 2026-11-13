"""第一批新增信息源：Bangumi 今日放送、Hacker News、GitHub Trending、少数派 / 36 氪 / 阮一峰 RSS。

用仿真响应验证字段映射；真实站点的可用性需在本机看板里实际抓取一次确认。
"""
import datetime
import json
import unittest
from pathlib import Path

from adapters.base import FetchContext
from adapters.registry import get_adapter
from core.settings import load_settings
from core.sources import load_sources

ROOT = Path(__file__).resolve().parent.parent
NEW = ["bangumi_calendar", "hacker_news", "github_trending", "sspai_feed", "kr36_feed", "ruanyifeng_blog"]


def _ctx(mapping):
    def http_get(url, headers=None, timeout=20.0):
        if url not in mapping:
            raise OSError(f"unexpected url {url}")
        val = mapping[url]
        return val if isinstance(val, bytes) else json.dumps(val).encode("utf-8")
    return FetchContext(http_get=http_get)


class NewSourcesConfigTests(unittest.TestCase):
    def test_configs_load_and_are_enabled(self):
        res = load_sources(load_settings(ROOT))
        self.assertEqual(res.errors, {})
        for sid in NEW:
            self.assertIn(sid, res.specs)
            self.assertTrue(res.specs[sid].enabled)


class NewSourcesFetchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.specs = load_sources(load_settings(ROOT)).specs

    def test_bangumi_keeps_today_and_sorts_by_rating(self):
        today = datetime.datetime.now().isoweekday()
        cal = [
            {"weekday": {"id": today}, "items": [
                {"id": 1, "name_cn": "低分番", "name": "Low", "url": "https://bgm.tv/subject/1",
                 "rating": {"score": 6.1}, "air_date": "2026-10-01", "summary": "s"},
                {"id": 2, "name_cn": "", "name": "High", "rating": {"score": 8.4}, "air_date": "2026-10-02"},
            ]},
            {"weekday": {"id": (today % 7) + 1}, "items": [
                {"id": 3, "name_cn": "别的天", "rating": {"score": 9.9}}]},
        ]
        res = get_adapter("script").fetch(self.specs["bangumi_calendar"], _ctx({
            "https://api.bgm.tv/calendar": cal}))
        self.assertIsNone(res.error)
        titles = [i.title for i in res.items]
        self.assertEqual(titles, ["High", "低分番"])  # 只取今天，按评分降序；无中文名时用原名
        self.assertEqual(res.items[0].url, "https://bgm.tv/subject/2")
        self.assertEqual(res.items[0].heat, "8.4")

    def test_hacker_news_uses_top_stories_and_skips_bad_items(self):
        base = "https://hacker-news.firebaseio.com/v0"
        ids = [11, 12, 13]
        mapping = {f"{base}/topstories.json": ids,
                   f"{base}/item/11.json": {"id": 11, "type": "story", "title": "A", "url": "https://a.example",
                                            "score": 120, "descendants": 33, "time": 1700000000},
                   f"{base}/item/12.json": {"id": 12, "type": "job", "title": "Job"},
                   f"{base}/item/13.json": {"id": 13, "type": "story", "title": "B", "score": 5}}
        res = get_adapter("script").fetch(self.specs["hacker_news"], _ctx(mapping))
        self.assertIsNone(res.error)
        self.assertEqual([i.title for i in res.items], ["A", "B"])
        self.assertEqual(res.items[0].heat, "120")
        self.assertEqual(res.items[0].url, "https://a.example")
        self.assertEqual(res.items[1].url, "https://news.ycombinator.com/item?id=13")

    def test_github_trending_html(self):
        html = b"""<html><body>
<article class="Box-row"><h2><a href="/owner/repo-one">owner / repo-one</a></h2>
<p>A useful tool</p><span class="d-inline-block float-sm-right">1,234 stars today</span></article>
<article class="Box-row"><h2><a href="/x/y">x / y</a></h2><p>Another</p>
<span class="d-inline-block float-sm-right">56 stars today</span></article>
</body></html>"""
        res = get_adapter("html_list").fetch(self.specs["github_trending"], _ctx({
            "https://github.com/trending": html}))
        self.assertIsNone(res.error)
        self.assertEqual(len(res.items), 2)
        self.assertEqual(res.items[0].url, "https://github.com/owner/repo-one")
        self.assertIn("owner", res.items[0].title)

    def test_rss_feeds_parse(self):
        rss = """<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>标题一</title><link>https://sspai.com/post/1</link><pubDate>Fri, 13 Nov 2026 08:00:00 GMT</pubDate>
<description>摘要</description></item></channel></rss>""".encode("utf-8")
        atom = """<?xml version="1.0" encoding="utf-8"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Atom 标题</title><link href="https://www.ruanyifeng.com/blog/2026/11/x.html"/>
<updated>2026-11-13T08:00:00Z</updated><summary>S</summary></entry></feed>""".encode("utf-8")
        ctx = _ctx({"https://sspai.com/feed": rss, "https://36kr.com/feed": rss,
                    "https://www.ruanyifeng.com/blog/atom.xml": atom})
        for sid in ("sspai_feed", "kr36_feed"):
            res = get_adapter("rss").fetch(self.specs[sid], ctx)
            self.assertIsNone(res.error)
            self.assertEqual(res.items[0].title, "标题一")
        res = get_adapter("rss").fetch(self.specs["ruanyifeng_blog"], ctx)
        self.assertEqual(res.items[0].url, "https://www.ruanyifeng.com/blog/2026/11/x.html")


if __name__ == "__main__":
    unittest.main()
