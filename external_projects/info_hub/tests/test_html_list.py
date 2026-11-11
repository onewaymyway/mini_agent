import unittest
from pathlib import Path

from adapters.base import FetchContext, SourceSpec
from adapters.html_list import HtmlListAdapter, parse_list

HTML = """
<html><body>
<div class="HotItem"><span class="t">热点一</span><a href="https://h.example/1">链接</a><span class="heat">100万</span></div>
<div class="HotItem"><span class="t">热点二</span><a href="https://h.example/2">链接</a></div>
<div class="HotItem"><span class="t"></span><a href="https://h.example/skip">空标题跳过</a></div>
</body></html>
"""

FIELDS = {"title": ".t", "url": "a@href", "heat": ".heat"}


class FakeBrowser:
    def __init__(self, html=None, error=None):
        self.html, self.error, self.calls = html, error, []

    def fetch_html(self, url, **kw):
        self.calls.append((url, kw))
        if self.error:
            raise self.error
        return self.html


def spec(**browser):
    return SourceSpec(id="h", name="h", category="news", kind="hot_list", adapter="html_list",
                      enabled=True, request={"url": "https://h.example/hot"},
                      parse={"list_selector": ".HotItem", "fields": FIELDS}, policy={},
                      schedule={}, browser=browser, tags=[], dir=Path("."))


class HtmlListTests(unittest.TestCase):
    def test_parse_list_extracts_title_url_heat_and_skips_empty(self):
        items = parse_list(HTML, ".HotItem", FIELDS, limit=10)
        self.assertEqual([i.title for i in items], ["热点一", "热点二"])
        self.assertEqual(items[0].url, "https://h.example/1")
        self.assertEqual(items[0].heat, "100万")
        self.assertIsNone(items[1].heat)
        self.assertEqual([i.rank for i in items], [1, 2])

    def test_limit_applies(self):
        self.assertEqual(len(parse_list(HTML, ".HotItem", FIELDS, limit=1)), 1)

    def test_fetch_without_browser_uses_http(self):
        ctx = FetchContext(http_get=lambda url, **kw: HTML.encode("utf-8"))
        res = HtmlListAdapter().fetch(spec(), ctx)
        self.assertTrue(res.ok)
        self.assertEqual(len(res.items), 2)

    def test_fetch_with_browser_passes_site_and_expired_pattern(self):
        fb = FakeBrowser(html=HTML)
        ctx = FetchContext(browser=fb, sites={"zh": {"expired_pattern": "/signin"}})
        res = HtmlListAdapter().fetch(spec(required=True, site="zh", wait_selector=".HotItem"), ctx)
        self.assertTrue(res.ok)
        url, kw = fb.calls[0]
        self.assertEqual(kw["site"], "zh")
        self.assertEqual(kw["expired_pattern"], "/signin")
        self.assertEqual(kw["wait_selector"], ".HotItem")

    def test_login_required_becomes_error_result(self):
        from core.browser_session import LoginRequired
        fb = FakeBrowser(error=LoginRequired("zh", "https://x/signin"))
        ctx = FetchContext(browser=fb, sites={})
        res = HtmlListAdapter().fetch(spec(required=True, site="zh"), ctx)
        self.assertFalse(res.ok)
        self.assertIn("重新登录", res.error)
        self.assertEqual(res.meta["login_required"], "zh")

    def test_validate_requires_site_when_browser_required(self):
        errs = HtmlListAdapter().validate(spec(required=True))
        self.assertTrue(any("browser.site" in e for e in errs))


if __name__ == "__main__":
    unittest.main()
