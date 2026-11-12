"""看板打开的待匹配登记：地址片段丢失时，插件凭当前地址取回条目上下文。"""
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

from core import feedback, pending_open
from core.settings import load_settings
from server import api
from server.api import ApiError
from server.feedback_receiver import load_or_create_token, make_server
from tests.helpers import make_project

XHS = "https://www.xiaohongshu.com/explore/6ad00f70000000001303c09b?xsec_token=AB2a8=&xsec_source=pc_feed"
KEY = "0123456789abcdef"


class PendingOpenTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp(prefix="ih_pending_"))

    def _reg(self, url=XHS, now=None, key=KEY):
        return pending_open.register(self.dir, url=url, item_key=key, source_id="xiaohongshu_hot",
                                     view="ranked", title="标题", now=now)

    def test_match_ignores_query_fragment_www_and_trailing_slash(self):
        self.assertTrue(self._reg())
        for url in ("https://xiaohongshu.com/explore/6ad00f70000000001303c09b/",
                    XHS + "#ihk=" + KEY,
                    "https://www.xiaohongshu.com/explore/6ad00f70000000001303c09b?xsec_token=NEW"):
            got = pending_open.lookup(self.dir, url)
            self.assertIsNotNone(got, url)
            self.assertEqual(got["item_key"], KEY)
            self.assertEqual(got["source_id"], "xiaohongshu_hot")
            self.assertEqual(got["view"], "ranked")

    def test_redirect_to_other_path_matches_by_last_segment(self):
        self._reg()
        got = pending_open.lookup(self.dir, "https://www.xiaohongshu.com/discovery/item/6ad00f70000000001303c09b")
        self.assertEqual(got["item_key"], KEY)

    def test_no_false_match(self):
        self._reg()
        self.assertIsNone(pending_open.lookup(self.dir, "https://www.xiaohongshu.com/explore"))
        self.assertIsNone(pending_open.lookup(self.dir, "https://other.com/explore/6ad00f70000000001303c09b0"))
        self.assertIsNone(pending_open.lookup(self.dir, "https://other.com/a/6ad00f70000000001303c09b"))
        self.assertIsNone(pending_open.lookup(self.dir, "chrome://newtab"))

    def test_short_last_segment_not_used_for_fallback(self):
        pending_open.register(self.dir, url="https://a.com/x/index", item_key=KEY, source_id="s", view="", title="")
        self.assertIsNone(pending_open.lookup(self.dir, "https://a.com/y/index"))

    def test_expiry_and_latest_wins(self):
        old = datetime.now().astimezone() - timedelta(hours=25)
        self._reg(now=old)
        self.assertIsNone(pending_open.lookup(self.dir, XHS))
        self._reg(key="aaaaaaaaaaaaaaaa")
        self._reg(key="bbbbbbbbbbbbbbbb")
        self.assertEqual(pending_open.lookup(self.dir, XHS)["item_key"], "bbbbbbbbbbbbbbbb")

    def test_cap_and_bad_input(self):
        for i in range(pending_open.MAX_ENTRIES + 20):
            pending_open.register(self.dir, url=f"https://a.com/p/item{i:06d}", item_key=KEY,
                                  source_id="s", view="", title="")
        data = json.loads(pending_open.pending_path(self.dir).read_text(encoding="utf-8"))
        self.assertEqual(len(data["entries"]), pending_open.MAX_ENTRIES)
        self.assertFalse(pending_open.register(self.dir, url="ftp://x/y", item_key=KEY, source_id="s", view="", title=""))
        self.assertFalse(pending_open.register(self.dir, url=XHS, item_key="bad", source_id="s", view="", title=""))

    def test_corrupt_file_is_ignored(self):
        pending_open.pending_path(self.dir).write_text("{not json", encoding="utf-8")
        self.assertIsNone(pending_open.lookup(self.dir, XHS))
        self.assertTrue(self._reg())


class ApiAndReceiverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = make_project({"news_a": {"id": "news_a", "name": "新闻", "category": "news", "kind": "feed",
                                            "adapter": "rss", "request": {"url": "https://n/feed"}}})
        cls.settings = load_settings(cls.root)
        cls.token = load_or_create_token(cls.settings)
        cls.server = make_server(cls.settings, port=0)
        cls.port = cls.server.server_address[1]
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _post(self, path, body, token=True):
        headers = {"Content-Type": "application/json"}
        if token:
            headers["X-InfoHub-Token"] = self.token
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=json.dumps(body).encode(),
                                     headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_dashboard_open_registers_context_and_receiver_resolves_it(self):
        key = feedback.item_key(XHS, "t")
        with mock.patch("core.dedicated_browser.open_url", return_value={"pid": 1}):
            api.open_in_dedicated_browser(self.settings, {"url": XHS, "source_id": "news_a", "item_key": key,
                                                          "view": "plain", "title": "t"})
        # 模拟站点丢掉了 #ihk 片段，也改了查询参数
        status, data = self._post("/v1/context", {"url": "https://www.xiaohongshu.com/explore/"
                                                         "6ad00f70000000001303c09b?xsec_token=OTHER"})
        self.assertEqual(status, 200)
        self.assertTrue(data["found"])
        self.assertEqual(data["context"]["item_key"], key)
        self.assertEqual(data["context"]["source_id"], "news_a")
        self.assertEqual(data["context"]["view"], "plain")

    def test_unknown_url_is_not_found_not_error(self):
        status, data = self._post("/v1/context", {"url": "https://nowhere.example/page/abcdefghijk"})
        self.assertEqual((status, data["found"], data["context"]), (200, False, None))

    def test_context_requires_token_and_valid_url(self):
        self.assertEqual(self._post("/v1/context", {"url": XHS}, token=False)[0], 401)
        self.assertEqual(self._post("/v1/context", {"url": "javascript:alert(1)"})[0], 400)
        self.assertEqual(self._post("/v1/context", {})[0], 400)
        with self.assertRaises(ApiError):
            api.resolve_context_api(self.settings, {"url": "x" * 5000})

    def test_manual_feedback_from_any_page_is_accepted(self):
        status, _ = self._post("/v1/feedback", {"action": "interested", "source_id": "manual",
                                                "url": "https://x.example/p/1", "title": "手动页面",
                                                "open_mode": "dedicated"})
        self.assertEqual(status, 200)
        self.assertTrue(any(e["source_id"] == "manual" for e in feedback.read_feedback(self.settings.paths.data_dir)))


if __name__ == "__main__":
    unittest.main()
