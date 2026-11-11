"""添加信息源相关 API（原 test_p7_http 的业务部分，改为直接调用 api 层）。"""
import unittest

from core.settings import load_settings
from server import api
from server.api import ApiError
from tests.helpers import make_project


class P7ApiTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({})
        self.settings = load_settings(self.root)

    def test_llm_status_reports_none_when_unconfigured(self):
        self.assertEqual(api.provider_status(self.settings)["source"], "none")

    def test_generate_without_provider_is_503(self):
        with self.assertRaises(ApiError) as cm:
            api.generate_source(self.settings, {"url": "https://x.example.com/", "name": "x"})
        self.assertEqual(cm.exception.status, 503)
        self.assertIn("providers", str(cm.exception))

    def test_drafts_list_and_missing_draft_404(self):
        self.assertEqual(api.list_drafts(self.settings)["drafts"], [])
        with self.assertRaises(ApiError) as cm:
            api.get_draft(self.settings, "nope_nope")
        self.assertEqual(cm.exception.status, 404)
        with self.assertRaises(ApiError) as cm:
            api.confirm_draft(self.settings, "nope_nope", {})
        self.assertEqual(cm.exception.status, 404)

    def test_create_form_then_listed(self):
        form = {"id": "http_src", "name": "表单源", "category": "news", "kind": "feed", "adapter": "rss",
                "url": "https://news.example.com/feed"}
        api.create_form(self.settings, {"source": form})
        self.assertIn("http_src", [s["id"] for s in api.list_sources(self.settings)["sources"]])


if __name__ == "__main__":
    unittest.main()
