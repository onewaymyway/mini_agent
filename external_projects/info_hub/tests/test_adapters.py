import unittest

from adapters.base import FetchContext, SourceSpec
from adapters.json_api import JsonApiAdapter
from adapters.registry import get_adapter, list_adapters
from adapters.rss import RssAdapter, parse_feed
from pathlib import Path

RSS2 = """<?xml version="1.0"?>
<rss version="2.0"><channel><title>x</title>
<item><title>第一条</title><link>https://a.example/1</link><description>摘要一</description><pubDate>Mon, 10 Nov 2026 08:00:00 GMT</pubDate></item>
<item><title>第二条</title><link>https://a.example/2</link></item>
<item><link>https://a.example/no-title</link></item>
</channel></rss>"""

ATOM = """<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom"><title>x</title>
<entry><title>Atom 标题</title><link href="https://b.example/e1" rel="alternate"/><summary>摘要</summary><updated>2026-11-10T00:00:00Z</updated></entry>
</feed>"""


def make_spec(**kw):
    base = dict(
        id="s", name="s", category="news", kind="feed", adapter="rss", enabled=True,
        request={}, parse={}, policy={}, schedule={}, browser={}, tags=[], dir=Path("."),
    )
    base.update(kw)
    return SourceSpec(**base)


class RssTests(unittest.TestCase):
    def test_parse_rss2_skips_items_without_title(self):
        items = parse_feed(RSS2.encode("utf-8"))
        self.assertEqual([i.title for i in items], ["第一条", "第二条"])
        self.assertEqual(items[0].url, "https://a.example/1")
        self.assertEqual(items[0].summary, "摘要一")
        self.assertEqual([i.rank for i in items], [1, 2])

    def test_parse_atom(self):
        items = parse_feed(ATOM.encode("utf-8"))
        self.assertEqual(items[0].title, "Atom 标题")
        self.assertEqual(items[0].url, "https://b.example/e1")
        self.assertEqual(items[0].published_at, "2026-11-10T00:00:00Z")

    def test_fetch_respects_max_items_and_reports_error(self):
        spec = make_spec(request={"url": "https://x"}, policy={"max_items": 1})
        ctx = FetchContext(http_get=lambda url, **kw: RSS2.encode("utf-8"))
        res = RssAdapter().fetch(spec, ctx)
        self.assertTrue(res.ok)
        self.assertEqual(len(res.items), 1)

        def boom(url, **kw):
            raise OSError("network down")
        res = RssAdapter().fetch(spec, FetchContext(http_get=boom))
        self.assertFalse(res.ok)
        self.assertIn("network down", res.error)

    def test_validate_requires_url(self):
        self.assertTrue(RssAdapter().validate(make_spec()))


class JsonApiTests(unittest.TestCase):
    PAYLOAD = (
        '{"data": {"list": ['
        '{"t": "热一", "link": "https://j.example/1", "hv": 1200, "desc": "d1"},'
        '{"t": "", "link": "https://j.example/skip"},'
        '{"t": "热二", "link": "https://j.example/2", "hv": null}'
        ']}}'
    ).encode("utf-8")

    def test_nested_items_path_and_fields(self):
        spec = make_spec(
            adapter="json_api",
            request={"url": "https://api"},
            parse={"items_path": "data.list",
                   "fields": {"title": "t", "url": "link", "heat": "hv", "summary": "desc"}},
        )
        ctx = FetchContext(http_get=lambda url, **kw: self.PAYLOAD)
        res = JsonApiAdapter().fetch(spec, ctx)
        self.assertTrue(res.ok)
        self.assertEqual([i.title for i in res.items], ["热一", "热二"])
        self.assertEqual(res.items[0].heat, "1200")
        self.assertIsNone(res.items[1].heat)
        self.assertEqual(res.items[0].summary, "d1")

    def test_items_path_not_list_is_error(self):
        spec = make_spec(adapter="json_api", request={"url": "u"},
                         parse={"items_path": "data", "fields": {"title": "t"}})
        ctx = FetchContext(http_get=lambda url, **kw: self.PAYLOAD)
        res = JsonApiAdapter().fetch(spec, ctx)
        self.assertFalse(res.ok)

    def test_validate_requires_title_field(self):
        spec = make_spec(adapter="json_api", request={"url": "u"}, parse={"fields": {"url": "link"}})
        self.assertTrue(JsonApiAdapter().validate(spec))


class RegistryTests(unittest.TestCase):
    def test_discovers_builtin_adapters(self):
        self.assertIn("rss", list_adapters())
        self.assertIn("json_api", list_adapters())
        self.assertIsNotNone(get_adapter("rss"))
        self.assertIsNone(get_adapter("does_not_exist"))


if __name__ == "__main__":
    unittest.main()
