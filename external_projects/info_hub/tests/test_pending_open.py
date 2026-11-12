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


class OpenApiAndPairingTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"news_a": {"id": "news_a", "name": "新闻", "category": "news", "kind": "feed",
                                             "adapter": "rss", "request": {"url": "https://n/feed"}}})
        self.settings = load_settings(self.root)

    def _opens(self):
        from core import evaluation
        from core.store import read_jsonl
        return [r for r in read_jsonl(evaluation.exposure_path(self.settings.paths.data_dir)) if r.get("kind") == "open"]

    def test_plain_open_has_no_fragment_registration_or_record(self):
        with mock.patch("core.dedicated_browser.open_url", return_value={"pid": 1}) as opener:
            api.open_in_dedicated_browser(self.settings, {"url": "https://n/feed"})
        self.assertEqual(opener.call_args[0][2], "https://n/feed")
        self.assertIsNone(pending_open.lookup(self.settings.paths.state_dir, "https://n/feed"))
        self.assertEqual(self._opens(), [])

    def test_item_open_records_once_after_success_only(self):
        key = feedback.item_key("https://n/9", "t")
        body = {"url": "https://n/9", "source_id": "news_a", "item_key": key, "view": "plain", "title": "t"}
        with mock.patch("core.dedicated_browser.open_url", side_effect=FileNotFoundError("无浏览器")):
            with self.assertRaises(ApiError):
                api.open_in_dedicated_browser(self.settings, body)
        self.assertEqual(self._opens(), [])  # 打开失败不记录
        with mock.patch("core.dedicated_browser.open_url", return_value={"pid": 1}):
            api.open_in_dedicated_browser(self.settings, body)
        self.assertEqual(len(self._opens()), 1)

    def test_half_filled_item_is_rejected(self):
        with self.assertRaises(ApiError):
            api.open_in_dedicated_browser(self.settings, {"url": "https://n/9", "source_id": "news_a"})
        with self.assertRaises(ApiError):
            api.open_in_dedicated_browser(self.settings, {"url": "https://n/9", "item_key": KEY})

    def test_pairing_window_is_one_shot_and_expires(self):
        from core import pairing
        sd = self.settings.paths.state_dir
        self.assertFalse(pairing.consume(sd, "chrome-extension://abc"))  # 未开启
        self.assertFalse(api.pairing_status(self.settings)["open"])
        st = api.pairing_start(self.settings)
        self.assertTrue(st["open"])
        self.assertGreater(st["remaining_seconds"], 200)
        self.assertTrue(pairing.consume(sd, "chrome-extension://abc"))
        self.assertFalse(pairing.consume(sd, "chrome-extension://abc"))  # 一次性
        self.assertEqual(api.pairing_status(self.settings)["paired"]["origin"], "chrome-extension://abc")
        later = datetime.now().astimezone() + timedelta(minutes=6)
        pairing.open_window(sd)
        self.assertFalse(pairing.consume(sd, "chrome-extension://abc", now=later))  # 过期
        api.pairing_start(self.settings)
        api.pairing_cancel(self.settings)
        self.assertFalse(api.pairing_status(self.settings)["open"])

    def test_corrupt_pairing_file_means_closed(self):
        from core import pairing
        path = pairing.pairing_path(self.settings.paths.state_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{bad", encoding="utf-8")
        self.assertFalse(api.pairing_status(self.settings)["open"])


class PairEndpointTests(ApiAndReceiverTests):
    """/v1/pair：只有看板开启窗口后，chrome-extension 来源才能取走令牌（一次性）。"""

    def _pair(self, origin="chrome-extension://abcdef"):
        headers = {"Origin": origin} if origin else {}
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/v1/pair", data=b"", headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, json.loads(resp.read().decode())
        except urllib.error.HTTPError as exc:
            return exc.code, json.loads(exc.read().decode())

    def test_pair_flow(self):
        api.pairing_cancel(self.settings)
        status, data = self._pair()
        self.assertEqual(status, 403)
        self.assertNotIn("token", data)
        api.pairing_start(self.settings)
        self.assertEqual(self._pair(origin="https://evil.example")[0], 403)  # 非扩展来源，且不消耗窗口
        self.assertEqual(self._pair(origin=None)[0], 403)
        status, data = self._pair()
        self.assertEqual((status, data["token"]), (200, self.token))
        self.assertEqual(self._pair()[0], 403)  # 一次性


if __name__ == "__main__":
    unittest.main()
