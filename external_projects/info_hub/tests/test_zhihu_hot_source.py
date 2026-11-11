"""内置知乎热榜信息源：配置合法 + 用仿真页面验证选择器与登录失效处理。"""
import unittest
from pathlib import Path

from adapters.base import FetchContext
from core.browser_session import LoginRequired
from core.settings import load_settings
from core.sources import load_sources

ROOT = Path(__file__).resolve().parent.parent

PAGE = """
<div class="HotList-list">
 <section class="HotItem"><div class="HotItem-index">1</div><div class="HotItem-content">
  <a href="https://www.zhihu.com/question/1" title="t1"><h2 class="HotItem-title">问题一</h2>
  <p class="HotItem-excerpt">摘要一</p></a><div class="HotItem-metrics">1234 万热度</div></div></section>
 <section class="HotItem"><div class="HotItem-content">
  <a href="https://www.zhihu.com/question/2"><h2 class="HotItem-title">问题二</h2></a>
  <div class="HotItem-metrics">99 万热度</div></div></section>
</div>"""


class FakeBrowser:
    def __init__(self, html=None, exc=None):
        self.html, self.exc, self.kwargs = html, exc, None

    def fetch_html(self, url, **kw):
        self.kwargs = kw
        if self.exc:
            raise self.exc
        return self.html


class ZhihuHotTests(unittest.TestCase):
    def setUp(self):
        st = load_settings(ROOT)
        from core.settings import load_sites
        self.sites = load_sites(ROOT)
        res = load_sources(st)
        self.assertNotIn("zhihu_hot", res.errors, res.errors)
        self.spec = res.specs["zhihu_hot"]
        from adapters.registry import get_adapter
        self.adapter = get_adapter(self.spec.adapter)

    def test_parse_items(self):
        b = FakeBrowser(PAGE)
        r = self.adapter.fetch(self.spec, FetchContext(browser=b, sites=self.sites))
        self.assertTrue(r.ok)
        self.assertEqual([i.title for i in r.items], ["问题一", "问题二"])
        self.assertEqual(r.items[0].url, "https://www.zhihu.com/question/1")
        self.assertEqual(r.items[0].heat, "1234 万热度")
        self.assertEqual(r.items[0].summary, "摘要一")
        self.assertEqual(b.kwargs["site"], "zhihu")
        self.assertEqual(b.kwargs["expired_pattern"], "/signin")

    def test_login_expired_is_reported(self):
        b = FakeBrowser(exc=LoginRequired("zhihu", "https://www.zhihu.com/signin"))
        r = self.adapter.fetch(self.spec, FetchContext(browser=b))
        self.assertFalse(r.ok)
        self.assertEqual(r.meta.get("login_required"), "zhihu")


if __name__ == "__main__":
    unittest.main()
