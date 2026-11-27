"""阶段 P14：订阅入口、sitemap 栏目、站内分类页、有头渲染回退（伪造站点与渲染器，不访问网络）。"""
import json
import unittest
from datetime import datetime
from pathlib import Path

from core import autoexplore, discovery
from core.explore_forms import (category_links, feed_links, parse_sitemap_urls, section_listings,
                                sitemap_locations)
from core.explore_render import HeadedRenderer
from core.settings import load_settings
from tests.helpers import make_project
from tests.test_autoexplore import FakeCtx, FakeLLM, PAGE, SITE

NOW = datetime(2026, 11, 27, 12, 0).astimezone()
RSS = ("<?xml version='1.0'?><rss version='2.0'><channel><title>t</title>"
       + "".join(f"<item><title>订阅标题{i}</title><link>https://good.example/a/{i}</link></item>" for i in range(1, 5))
       + "</channel></rss>").encode("utf-8")
SHELL = b"<html><body><div id='app'></div><script src='/app.js'></script></body></html>"


class FakeRenderer:
    def __init__(self, html):
        self.html_out = html
        self.urls = []

    def html(self, url):
        self.urls.append(url)
        return self.html_out


class HelperTests(unittest.TestCase):
    def test_feed_links_resolve_relative_and_keep_https_only(self):
        html = ('<link rel="alternate" type="application/rss+xml" href="/feed.xml">'
                '<link rel="alternate" type="application/atom+xml" href="https://good.example/atom">'
                '<link rel="alternate" type="text/html" href="/x">'
                '<link rel="alternate" type="application/rss+xml" href="http://insecure.example/f">')
        self.assertEqual(feed_links(html, "https://good.example/"),
                         ["https://good.example/feed.xml", "https://good.example/atom"])

    def test_sitemap_locations_only_https(self):
        robots = "User-agent: *\nSitemap: https://good.example/s.xml\nSitemap: http://bad.example/s.xml\n"
        self.assertEqual(sitemap_locations(robots), ["https://good.example/s.xml"])

    def test_section_listings_need_repetition_and_skip_utility_paths(self):
        urls = [f"https://good.example/news/{i}" for i in range(3)] + \
               [f"https://good.example/tag/{i}" for i in range(5)] + ["https://good.example/lonely/1"]
        out = section_listings(urls)
        self.assertEqual([s["section"] for s in out], ["news"])
        self.assertEqual(out[0]["url"], "https://good.example/news/")

    def test_category_links_single_segment_same_site(self):
        html = ('<a href="/tech/">科技</a><a href="/tech/x/y">深层</a><a href="https://other.example/a">外站</a>'
                '<a href="/login">登录</a><a href="/">首页</a>')
        self.assertEqual(category_links(html, "https://good.example/"), ["https://good.example/tech/"])

    def test_parse_sitemap(self):
        xml = "<urlset><url><loc>https://good.example/news/1</loc></url><url><loc> https://good.example/news/2 </loc></url></urlset>"
        self.assertEqual(parse_sitemap_urls(xml), ["https://good.example/news/1", "https://good.example/news/2"])


class FlowTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        (self.root / "config" / "seed_sites.yaml").write_text(f"sites:\n  - url: {SITE}\n", encoding="utf-8")
        self.settings = load_settings(self.root)
        self.data = self.settings.paths.data_dir

    def _cfg(self, **kw):
        lines = "autoexplore:\n" + "".join(f"  {k}: {v}\n" for k, v in kw.items())
        (self.root / "config" / "discovery.yaml").write_text(lines, encoding="utf-8")

    def test_feed_discovered_without_any_model(self):
        home = b'<html><head><link rel="alternate" type="application/rss+xml" href="/feed.xml"></head><body></body></html>'
        ctx = FakeCtx({SITE: home, f"{SITE}feed.xml": RSS})
        out = autoexplore.run(self.settings, None, ctx=ctx, now=NOW)
        self.assertEqual(len(out["added"]), 1, out)
        cand = out["added"][0]
        self.assertEqual(cand["template_id"], "auto_feed")
        self.assertEqual(cand["source"]["adapter"], "rss")
        self.assertFalse(cand["source"]["enabled"])
        self.assertEqual(discovery.list_candidates(self.data)[0]["origin"], "auto_explore")

    def test_sitemap_section_becomes_its_own_candidate(self):
        robots = b"User-agent: *\nSitemap: https://good.example/sitemap.xml\n"
        sitemap = ("<urlset>" + "".join(f"<url><loc>https://good.example/news/{i}</loc></url>" for i in range(3))
                   + "</urlset>").encode()
        ctx = FakeCtx({SITE: b"<html><body>empty</body></html>", f"{SITE}robots.txt": robots,
                       f"{SITE}sitemap.xml": sitemap, f"{SITE}news/": PAGE.encode(),
                       "https://good.example/news/": PAGE.encode()})
        out = autoexplore.run(self.settings, FakeLLM("https://good.example/news/"), ctx=ctx, now=NOW)
        ids = [c["id"] for c in out["added"]]
        self.assertIn("auto:good.example#news", ids, out)
        self.assertNotIn("auto:good.example", ids)  # 首页没有列表结构，不生成首页候选

    def test_category_page_explored_as_listing(self):
        home = '<html><body><a href="/tech/">科技</a></body></html>'.encode("utf-8")
        ctx = FakeCtx({SITE: home, "https://good.example/tech/": PAGE.encode()})
        out = autoexplore.run(self.settings, FakeLLM("https://good.example/tech/"), ctx=ctx, now=NOW)
        self.assertTrue(any(c["id"] == "auto:good.example#tech" for c in out["added"]), out)

    def test_headed_render_used_when_no_structure(self):
        self._cfg(render="headed", render_daily=5)
        renderer = FakeRenderer(PAGE)
        ctx = FakeCtx({SITE: SHELL})
        out = autoexplore.run(self.settings, FakeLLM(SITE), ctx=ctx, now=NOW, renderer=renderer)
        self.assertEqual(renderer.urls, [SITE])
        self.assertEqual(len(out["added"]), 1, out)

    def test_render_off_never_calls_renderer(self):
        self._cfg(render="off", render_daily=5)
        renderer = FakeRenderer(PAGE)
        out = autoexplore.run(self.settings, FakeLLM(SITE), ctx=FakeCtx({SITE: SHELL}), now=NOW, renderer=renderer)
        self.assertEqual(renderer.urls, [])
        self.assertEqual(out["added"], [])

    def test_render_daily_cap_respected(self):
        self._cfg(render="headed", render_daily=0)
        renderer = FakeRenderer(PAGE)
        autoexplore.run(self.settings, FakeLLM(SITE), ctx=FakeCtx({SITE: SHELL}), now=NOW, renderer=renderer)
        self.assertEqual(renderer.urls, [])

    def test_render_logged(self):
        self._cfg(render="headed", render_daily=5)
        autoexplore.run(self.settings, FakeLLM(SITE), ctx=FakeCtx({SITE: SHELL}), now=NOW,
                        renderer=FakeRenderer(PAGE))
        rows = [json.loads(x) for x in autoexplore.log_path(self.data).read_text().splitlines()]
        self.assertTrue(any(r["step"] == "render" for r in rows))


class HeadedRendererTests(unittest.TestCase):
    def test_headed_isolated_profile_and_cleanup(self):
        seen = {}

        class FakeSession:
            def __init__(self, profile_dir, status_path):
                seen["profile"] = Path(profile_dir)
                seen["status"] = Path(status_path)
                Path(profile_dir).mkdir(parents=True, exist_ok=True)

            def fetch_html(self, url, *, headless=True, timeout_ms=0, **_):
                seen["headless"] = headless
                seen["url"] = url
                return "<html>ok</html>"

        import tempfile
        scratch = Path(tempfile.mkdtemp())
        r = HeadedRenderer(scratch, session_factory=FakeSession)
        self.assertEqual(r.html("https://good.example/"), "<html>ok</html>")
        self.assertFalse(seen["headless"])                 # 有头
        self.assertNotIn("browser", str(seen["profile"]))  # 不是共享登录态目录
        self.assertFalse(seen["profile"].parent.exists())  # 用完即删


if __name__ == "__main__":
    unittest.main()
