"""专用浏览器启动、打开地址拼接、本地接收服务（令牌、来源、校验）与看板连接信息。"""
import json
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

from core import dedicated_browser as db
from core import feedback
from core.settings import load_settings
from server import api
from server.api import ApiError
from server.feedback_receiver import load_or_create_token, make_server
from tests.helpers import make_project

ORIGIN = "chrome-extension://abcdefghijklmnop"


class OpenUrlTests(unittest.TestCase):
    def test_build_open_url_adds_fragment_and_replaces_existing(self):
        u = db.build_open_url("https://n.test/a?x=1#old", item_key="0123456789abcdef",
                              source_id="news_a", view="ranked", title="标题 甲")
        self.assertTrue(u.startswith("https://n.test/a?x=1#ihk=0123456789abcdef&ihs=news_a&ihv=ranked&iht="))
        self.assertNotIn("#old", u)
        self.assertIn("%E6%A0%87%E9%A2%98", u)  # 标题已编码

    def test_launch_command_uses_dedicated_profile(self):
        cmd = db.launch_command("/bin/chrome", Path("/tmp/prof"), "https://x/#a")
        self.assertIn("--user-data-dir=/tmp/prof", cmd)
        self.assertEqual(cmd[-1], "https://x/#a")

    def test_find_executable_respects_configured_path(self):
        with mock.patch.object(db, "_candidates", return_value=[]), mock.patch("shutil.which", return_value=None):
            self.assertIsNone(db.find_executable(""))
            self.assertIsNone(db.find_executable("/nonexistent/chrome"))
            self.assertIsNotNone(db.find_executable(__file__))  # 存在的路径即可

    def test_open_url_creates_profile_and_launches(self):
        root = make_project({})
        cfg = {"executable": __file__, "profile_dir": "browser/dedicated_profile"}
        popen = mock.Mock(return_value=mock.Mock(pid=4321))
        out = db.open_url(root, cfg, "https://x/#ihk=0123456789abcdef", popen=popen)
        self.assertEqual(out["pid"], 4321)
        self.assertTrue((root / "browser" / "dedicated_profile").is_dir())
        args = popen.call_args.args[0]
        self.assertIn(f"--user-data-dir={root / 'browser' / 'dedicated_profile'}", args)

    def test_open_url_without_browser_raises(self):
        root = make_project({})
        with mock.patch.object(db, "find_executable", return_value=None):
            with self.assertRaises(FileNotFoundError):
                db.open_url(root, {"executable": "", "profile_dir": "p"}, "https://x/")

    def test_load_config_defaults(self):
        root = make_project({})
        cfg = db.load_config(root)
        self.assertEqual(cfg["receiver_port"], 8765)
        self.assertEqual(cfg["profile_dir"], "browser/dedicated_profile")


class ApiOpenTests(unittest.TestCase):
    def setUp(self):
        self.root = make_project({"news_a": {"id": "news_a", "name": "新闻", "category": "news", "kind": "feed",
                                             "adapter": "rss", "request": {"url": "https://n/feed"}}})
        self.settings = load_settings(self.root)
        self.key = feedback.item_key("https://n/1", "甲")

    def test_rejects_bad_input(self):
        base = {"url": "https://n/1", "source_id": "news_a", "item_key": self.key, "title": "甲"}
        for patch in ({"url": "file:///etc/passwd"}, {"source_id": "../x"}, {"item_key": "bad"}):
            with self.subTest(patch=patch), self.assertRaises(ApiError) as cm:
                api.open_in_dedicated_browser(self.settings, {**base, **patch})
            self.assertEqual(cm.exception.status, 400)

    def test_missing_browser_is_503(self):
        with mock.patch.object(db, "find_executable", return_value=None):
            with self.assertRaises(ApiError) as cm:
                api.open_in_dedicated_browser(self.settings, {"url": "https://n/1", "source_id": "news_a",
                                                              "item_key": self.key, "title": "甲"})
        self.assertEqual(cm.exception.status, 503)

    def test_launch_through_api(self):
        with mock.patch.object(db, "open_url", return_value={"pid": 1}) as m:
            out = api.open_in_dedicated_browser(self.settings, {"url": "https://n/1", "source_id": "news_a",
                                                                "item_key": self.key, "title": "甲", "view": "plain"})
        self.assertEqual(out["pid"], 1)
        target = m.call_args.args[2]
        self.assertIn(f"ihk={self.key}", target)

    def test_receiver_info_exposes_token_and_url(self):
        info = api.receiver_info(self.settings)
        self.assertEqual(info["url"], "http://127.0.0.1:8765")
        self.assertEqual(info["token"], load_or_create_token(self.settings))


class ReceiverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.root = make_project({"news_a": {"id": "news_a", "name": "新闻", "category": "news", "kind": "feed",
                                            "adapter": "rss", "request": {"url": "https://n/feed"}}})
        cls.settings = load_settings(cls.root)
        cls.token = load_or_create_token(cls.settings)
        cls.server = make_server(cls.settings, port=0)
        cls.port = cls.server.server_address[1]
        cls.thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def _req(self, path, body=None, headers=None, method=None):
        data = None if body is None else json.dumps(body).encode("utf-8")
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}{path}", data=data,
                                     headers={"Content-Type": "application/json", **(headers or {})},
                                     method=method or ("POST" if data is not None else "GET"))
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status, dict(resp.headers), json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            return exc.code, dict(exc.headers), json.loads(exc.read().decode("utf-8") or "{}")

    def _body(self):
        return {"action": "interested", "source_id": "news_a", "url": "https://n/9", "title": "接收测试"}

    def test_ping_needs_no_token(self):
        status, _, data = self._req("/v1/ping")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])

    def test_write_requires_valid_token(self):
        status, _, _ = self._req("/v1/feedback", self._body())
        self.assertEqual(status, 401)
        status, _, _ = self._req("/v1/feedback", self._body(), {"X-InfoHub-Token": "wrong"})
        self.assertEqual(status, 401)

    def test_feedback_stored_with_extension_origin(self):
        status, headers, data = self._req("/v1/feedback", self._body(),
                                          {"X-InfoHub-Token": self.token, "Origin": ORIGIN})
        self.assertEqual(status, 200)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), ORIGIN)
        events = feedback.read_feedback(self.settings.paths.data_dir)
        self.assertTrue(any(e["title"] == "接收测试" for e in events))

    def test_foreign_origin_rejected(self):
        status, headers, _ = self._req("/v1/feedback", self._body(),
                                       {"X-InfoHub-Token": self.token, "Origin": "https://evil.example"})
        self.assertEqual(status, 403)
        self.assertNotIn("Access-Control-Allow-Origin", headers)

    def test_preflight_only_for_extension(self):
        status, headers, _ = self._req("/v1/feedback", None, {"Origin": ORIGIN}, method="OPTIONS")
        self.assertEqual(status, 204)
        self.assertEqual(headers.get("Access-Control-Allow-Origin"), ORIGIN)

    def test_invalid_body_and_unknown_path(self):
        status, _, _ = self._req("/v1/feedback", {"action": "nope", "source_id": "news_a", "url": "https://n/1"},
                                 {"X-InfoHub-Token": self.token})
        self.assertEqual(status, 400)
        status, _, _ = self._req("/v1/other", self._body(), {"X-InfoHub-Token": self.token})
        self.assertEqual(status, 404)

    def test_oversized_body_rejected(self):
        big = {"action": "later", "source_id": "news_a", "url": "https://n/1", "note": "字" * 20000}
        status, _, _ = self._req("/v1/feedback", big, {"X-InfoHub-Token": self.token})
        self.assertEqual(status, 413)

    def test_open_endpoint_records_exposure(self):
        key = feedback.item_key("https://n/9", "接收测试")
        status, _, _ = self._req("/v1/open", {"source_id": "news_a", "item_key": key, "view": "plain",
                                              "mode": "dedicated"}, {"X-InfoHub-Token": self.token, "Origin": ORIGIN})
        self.assertEqual(status, 200)

    def test_only_loopback_bind(self):
        with self.assertRaises(ValueError):
            make_server(self.settings, port=0, host="0.0.0.0")


if __name__ == "__main__":
    unittest.main()
