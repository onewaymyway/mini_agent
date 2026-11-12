"""财经信息源：东方财富要闻（html_list，普通抓取）、网易财经（html_list，浏览器渲染）。

沙箱无法访问这两个站点，这里用仿真页面验证选择器、链接补全、编码与配置；真实页面需在本机验证。
"""
import unittest
from pathlib import Path

from adapters.base import FetchContext
from adapters.html_list import HtmlListAdapter, parse_list
from core.settings import load_settings
from core.sources import load_sources

ROOT = Path(__file__).resolve().parent.parent

EASTMONEY_HTML = """
<html><body>
<nav><a href="https://stock.eastmoney.com/">股票</a><a href="https://data.eastmoney.com/center/">数据中心</a>
<a href="https://quote.eastmoney.com/center/gridlist.html">行情</a></nav>
<div class="feed">
  <a href="https://finance.eastmoney.com/a/202611113928576774.html">克里姆林宫称普京愿与欧洲方面接触</a>
  <a href="https://finance.eastmoney.com/a/202611113928575356.html">摩尔线程MDC 2026定档12月</a>
  <a href="//fund.eastmoney.com/a/202611113928500001.html">基金周报：科技风格回调</a>
  <a href="/a/202611113928500002.html">相对链接不属于 eastmoney.com/a/ 形态，应忽略</a>
  <a href="https://quote.eastmoney.com/unify/r/0.300059">东方财富 300059</a>
  <a href="https://acttg.eastmoney.com/pub/x"><img src="x.png"></a>
  <a href="https://finance.eastmoney.com/a/202611113928509999.html"><img src="p.png"></a>
</div></body></html>
"""

NETEASE_MONEY_HTML = """
<html><body>
<div class="nav"><a href="https://money.163.com/stock/">股票</a><a href="https://money.163.com/special/xx/">专题</a></div>
<ul>
  <li><a href="https://www.163.com/money/article/KABCDEFG00259K2L.html">央行发布三季度货币政策执行报告</a></li>
  <li><a href="//www.163.com/money/article/KABCDEFH00259K2L.html">协议头链接也应补全为 https</a></li>
  <li><a href="http://money.163.com/26/1112/10/KABCDEFI00259K2L.html">旧式地址的财经报道</a></li>
  <li><a href="https://www.163.com/dy/article/KXYZ.html">网易号文章，不属于财经频道，应忽略</a></li>
  <li><a href="https://www.163.com/money/article/KABCDEFJ00259K2L.html"><img src="a.png"></a></li>
</ul></body></html>
"""


class FinanceSourcesTests(unittest.TestCase):
    def setUp(self):
        self.settings = load_settings(ROOT)
        self.loaded = load_sources(self.settings)

    def test_specs_load_without_errors(self):
        self.assertEqual(self.loaded.errors, {})
        for sid in ("eastmoney_news", "netease_money"):
            spec = self.loaded.specs[sid]
            self.assertTrue(spec.enabled)
            self.assertEqual(spec.adapter, "html_list")
            self.assertEqual(spec.category, "news")
        self.assertTrue(self.settings.categories)

    def test_category_exists(self):
        ids = {c["id"] for c in self.settings.categories}
        for sid in ("eastmoney_news", "netease_money"):
            self.assertIn(self.loaded.specs[sid].category, ids)

    def test_eastmoney_extracts_only_article_links(self):
        spec = self.loaded.specs["eastmoney_news"]
        items = parse_list(EASTMONEY_HTML, spec.parse["list_selector"], spec.parse["fields"], 100)
        self.assertEqual([i.title for i in items], [
            "克里姆林宫称普京愿与欧洲方面接触", "摩尔线程MDC 2026定档12月", "基金周报：科技风格回调"])
        self.assertTrue(all("eastmoney.com/a/" in i.url for i in items))

    def test_eastmoney_protocol_relative_link_is_completed_by_adapter(self):
        spec = self.loaded.specs["eastmoney_news"]
        ctx = FetchContext(http_get=lambda u, **k: EASTMONEY_HTML.encode("utf-8"))
        res = HtmlListAdapter().fetch(spec, ctx)
        self.assertTrue(res.ok)
        self.assertEqual(res.items[2].url, "https://fund.eastmoney.com/a/202611113928500001.html")
        self.assertEqual(res.meta["rows_matched"], 3)

    def test_gbk_page_is_decoded_not_garbled(self):
        spec = self.loaded.specs["eastmoney_news"]
        ctx = FetchContext(http_get=lambda u, **k: EASTMONEY_HTML.encode("gb18030"))
        res = HtmlListAdapter().fetch(spec, ctx)
        self.assertTrue(res.ok)
        self.assertEqual(res.items[0].title, "克里姆林宫称普京愿与欧洲方面接触")

    def test_netease_money_selector_and_url_completion(self):
        spec = self.loaded.specs["netease_money"]
        self.assertTrue(spec.browser.get("required"))
        self.assertIn(spec.browser["site"], self.settings_sites())

        class FakeBrowser:
            def fetch_html(self, url, **kw):
                self.kw = kw
                return NETEASE_MONEY_HTML

        browser = FakeBrowser()
        res = HtmlListAdapter().fetch(spec, FetchContext(browser=browser, sites=self.settings_sites()))
        self.assertTrue(res.ok, res.error)
        self.assertEqual([i.title for i in res.items], [
            "央行发布三季度货币政策执行报告", "协议头链接也应补全为 https", "旧式地址的财经报道"])
        self.assertEqual(res.items[1].url, "https://www.163.com/money/article/KABCDEFH00259K2L.html")
        self.assertEqual(res.items[2].url, "http://money.163.com/26/1112/10/KABCDEFI00259K2L.html")
        self.assertEqual(browser.kw["wait_selector"], spec.browser["wait_selector"])

    def settings_sites(self):
        import yaml
        data = yaml.safe_load((ROOT / "config" / "sites.yaml").read_text(encoding="utf-8"))
        return data["sites"]


if __name__ == "__main__":
    unittest.main()
