"""第一阶段：反馈存储、正文抽取、打开方式偏好、API 层约束。"""
import json
import unittest
from pathlib import Path
from unittest import mock

from core import feedback
from core.reader import default_open_mode, effective_open_mode, extract_article, save_open_mode
from core.settings import load_settings
from server import api
from server.api import ApiError
from tests.helpers import make_project

HTML = """<html><head><title>测试文章</title><script>var x=1;</script></head>
<body><nav><p>导航文字</p></nav>
<article><h2>第一节</h2><p>正文第一段，内容足够长。</p>
<img src="/img/a.png" alt="配图"><p>正文第二段。</p></article>
<footer><p>页脚</p></footer></body></html>"""


class FeedbackStoreTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        self.data = self.root / "data"

    def test_item_key_is_stable_across_fragment_and_trailing_slash(self):
        a = feedback.item_key("https://Example.com/a/?x=1#top")
        b = feedback.item_key("https://example.com/a?x=1")
        self.assertEqual(a, b)
        self.assertEqual(len(a), 16)

    def test_record_and_read_append_only(self):
        body = {"action": "interested", "source_id": "news_a", "url": "https://n/1", "title": "甲"}
        feedback.record_feedback(self.data, body)
        feedback.record_feedback(self.data, {**body, "action": "not_interested", "reasons": ["shallow"]})
        events = feedback.read_feedback(self.data, feedback.item_key("https://n/1"))
        self.assertEqual([e["action"] for e in events], ["interested", "not_interested"])
        self.assertEqual(events[1]["reasons"], ["shallow"])
        self.assertEqual(feedback.summarize(events), {"interested": 1, "not_interested": 1, "later": 0})

    def test_reasons_and_note_are_optional(self):
        e = feedback.validate_event({"action": "later", "source_id": "s", "url": "https://n/2"})
        self.assertEqual(e["reasons"], [])
        self.assertEqual(e["note"], "")

    def test_invalid_inputs_rejected(self):
        bad = [
            {"action": "love", "source_id": "s", "url": "https://n/1"},
            {"action": "later", "source_id": "s"},
            {"action": "later", "source_id": "../x", "url": "https://n/1"},
            {"action": "later", "source_id": "s", "url": "javascript:alert(1)"},
            {"action": "later", "source_id": "s", "url": "https://n/1", "reasons": ["bogus"]},
        ]
        for body in bad:
            with self.subTest(body=body), self.assertRaises(ValueError):
                feedback.validate_event(body)

    def test_unknown_open_mode_is_dropped_not_stored(self):
        e = feedback.validate_event({"action": "later", "source_id": "s", "url": "https://n/1",
                                     "open_mode": "weird"})
        self.assertEqual(e["open_mode"], "")

    def test_note_is_truncated(self):
        e = feedback.validate_event({"action": "later", "source_id": "s", "url": "https://n/1",
                                     "note": "字" * 1000})
        self.assertEqual(len(e["note"]), feedback.MAX_NOTE_CHARS)


class ExtractArticleTests(unittest.TestCase):
    def test_extracts_title_paragraphs_images_and_drops_chrome(self):
        art = extract_article(HTML, "https://site.test/post/1")
        self.assertEqual(art["title"], "测试文章")
        texts = [b.get("text") for b in art["blocks"] if b["type"] in ("p", "h")]
        self.assertIn("第一节", texts)
        self.assertIn("正文第一段，内容足够长。", texts)
        self.assertNotIn("导航文字", texts)
        self.assertNotIn("页脚", texts)
        self.assertNotIn("var x=1;", " ".join(texts))
        imgs = [b for b in art["blocks"] if b["type"] == "img"]
        self.assertEqual(imgs[0]["src"], "https://site.test/img/a.png")

    def test_empty_html_is_safe(self):
        art = extract_article("", "")
        self.assertEqual(art["blocks"], [])
        self.assertEqual(art["text_chars"], 0)


class OpenModePrefTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        (self.root / "config" / "settings.yaml").write_text(
            "paths:\n  sources_dir: sources\n  data_dir: data\n  reports_dir: reports\n"
            "  browser_profile_dir: browser/profile\nview:\n  default_open_mode: iframe\n",
            encoding="utf-8")
        self.settings = load_settings(self.root)

    def test_config_default_then_runtime_override(self):
        self.assertEqual(default_open_mode(self.root), "iframe")
        self.assertEqual(effective_open_mode(self.settings), "iframe")
        save_open_mode(self.settings, "newtab")
        self.assertEqual(effective_open_mode(self.settings), "newtab")
        # 配置默认值保持不变
        self.assertEqual(default_open_mode(self.root), "iframe")

    def test_invalid_config_falls_back_to_reader(self):
        (self.root / "config" / "settings.yaml").write_text("view:\n  default_open_mode: bogus\n", encoding="utf-8")
        self.assertEqual(default_open_mode(self.root), "reader")

    def test_save_rejects_unknown_mode(self):
        with self.assertRaises(ValueError):
            save_open_mode(self.settings, "popup")


class ApiTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({
            "news_a": {"id": "news_a", "name": "新闻", "category": "news", "kind": "feed",
                       "adapter": "rss", "request": {"url": "https://n/feed"}},
            "xhs": {"id": "xhs", "name": "小红书", "category": "news", "kind": "feed",
                    "adapter": "html_list", "request": {"url": "https://x/explore"},
                    "browser": {"required": True, "site": "xiaohongshu", "headless": False},
                    "parse": {"list_selector": "section.note-item", "fields": {"title": "a.title"}}},
        })
        self.settings = load_settings(self.root)

    def test_record_feedback_api_maps_validation_to_400(self):
        with self.assertRaises(ApiError) as cm:
            api.record_feedback(self.settings, {"action": "nope", "source_id": "news_a", "url": "https://n/1"})
        self.assertEqual(cm.exception.status, 400)

    def test_record_and_query_feedback_via_api(self):
        api.record_feedback(self.settings, {"action": "interested", "source_id": "news_a",
                                            "url": "https://n/1", "title": "甲", "reasons": []})
        key = feedback.item_key("https://n/1", "甲")
        out = api.item_feedback(self.settings, key)
        self.assertEqual(out["counts"]["interested"], 1)

    def test_item_feedback_rejects_bad_key(self):
        with self.assertRaises(ApiError):
            api.item_feedback(self.settings, "../../etc")

    def test_read_article_rejects_non_http(self):
        with self.assertRaises(ApiError) as cm:
            api.read_article(self.settings, "news_a", "file:///etc/passwd")
        self.assertEqual(cm.exception.status, 400)

    def test_read_article_plain_source_uses_http(self):
        with mock.patch("adapters.base.default_http_get", return_value=HTML.encode("utf-8")):
            out = api.read_article(self.settings, "news_a", "https://n/post")
        self.assertEqual(out["title"], "测试文章")
        self.assertTrue(any(b["type"] == "p" for b in out["blocks"]))

    def test_read_article_browser_source_passes_headless_and_site(self):
        fake = mock.Mock()
        fake.return_value.fetch_html.return_value = HTML
        with mock.patch("core.browser_session.BrowserSession", fake):
            api.read_article(self.settings, "xhs", "https://x/explore/1")
        kwargs = fake.return_value.fetch_html.call_args.kwargs
        self.assertEqual(kwargs["site"], "xiaohongshu")
        self.assertFalse(kwargs["headless"])

    def test_read_article_login_and_risk_map_to_409(self):
        from core.browser_session import LoginRequired, RiskControlBlocked
        for exc in (LoginRequired("xiaohongshu", "https://x/login"),
                    RiskControlBlocked("xiaohongshu", "https://x/err", "300012", "风险")):
            fake = mock.Mock()
            fake.return_value.fetch_html.side_effect = exc
            with self.subTest(exc=type(exc).__name__), mock.patch("core.browser_session.BrowserSession", fake):
                with self.assertRaises(ApiError) as cm:
                    api.read_article(self.settings, "xhs", "https://x/explore/1")
                self.assertEqual(cm.exception.status, 409)

    def test_set_default_open_mode_api(self):
        out = api.set_default_open_mode(self.settings, "iframe")
        self.assertEqual(out["open_mode"], "iframe")
        with self.assertRaises(ApiError):
            api.set_default_open_mode(self.settings, "popup")


if __name__ == "__main__":
    unittest.main()
