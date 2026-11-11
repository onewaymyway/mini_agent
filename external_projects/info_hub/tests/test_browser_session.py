"""使用真实无头 Chromium + 本地 HTTP 服务器验证：共享 profile 的登录态跨实例复用。

若环境中 Chromium 无法启动，测试自动跳过（不影响其他测试）。
"""
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from core import store
from core.browser_session import BrowserSession, LoginRequired

PAGES = {
    "/set": (200, "<html><body>set</body></html>", [("Set-Cookie", "sid=ok; Path=/; Max-Age=86400")]),
    "/login": (200, "<html><body>login page</body></html>", []),
    "/hot": (200, '<html><body><div class="item"><span>热榜A</span></div></body></html>', []),
}


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *a):  # 静默
        pass

    def do_GET(self):
        cookie = self.headers.get("Cookie", "")
        if self.path == "/check":
            if "sid=ok" in cookie:
                status, body, headers = 200, "<html><body><div class='me'>已登录</div></body></html>", []
            else:
                self.send_response(302)
                self.send_header("Location", "/login")
                self.end_headers()
                return
        elif self.path in PAGES:
            status, body, headers = PAGES[self.path]
        else:
            status, body, headers = 404, "not found", []
        self.send_response(status)
        for k, v in headers:
            self.send_header(k, v)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body.encode("utf-8"))


class ChromiumTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.server = HTTPServer(("127.0.0.1", 0), Handler)
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                b = p.chromium.launch(headless=True)
                b.close()
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"Chromium 不可用: {exc}")

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        cls.server.server_close()

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.profile = self.root / "browser" / "profile"
        self.status = self.root / "browser" / "login_status.json"

    def tearDown(self):
        self._tmp.cleanup()

    def session(self):
        return BrowserSession(self.profile, self.status)


class SharedProfileTests(ChromiumTestCase):
    def test_login_cookie_reused_by_new_session_on_same_profile(self):
        # 第一次：模拟登录写入 cookie
        self.session().fetch_html(f"{self.base}/set", headless=True)
        # 第二次：全新会话实例、同一 profile，不再登录即可访问需要登录的页面
        html = self.session().fetch_html(
            f"{self.base}/check", site="s", headless=True, expired_pattern="/login"
        )
        self.assertIn("已登录", html)

    def test_missing_login_raises_and_records_expired(self):
        with self.assertRaises(LoginRequired):
            self.session().fetch_html(
                f"{self.base}/check", site="s", headless=True, expired_pattern="/login"
            )
        data = store.read_json(self.status)
        self.assertEqual(data["s"]["status"], "expired")

    def test_interactive_login_records_logged_in(self):
        # 用无头模式替代有头模式，login_url 指向会设置 cookie 的页面，等待回调为空操作
        session = BrowserSession(self.profile, self.status,
                                 launcher=lambda headless: BrowserSession._default_launcher(
                                     self.session(), True))
        status = session.interactive_login(
            "s", login_url=f"{self.base}/set", check_url=f"{self.base}/check",
            expired_pattern="/login", wait_for_user=lambda prompt: None,
        )
        self.assertEqual(status, "logged_in")
        self.assertEqual(store.read_json(self.status)["s"]["status"], "logged_in")

    def test_html_list_end_to_end_with_real_browser(self):
        from adapters.base import FetchContext, SourceSpec
        from adapters.html_list import HtmlListAdapter
        spec = SourceSpec(id="h", name="h", category="news", kind="hot_list", adapter="html_list",
                          enabled=True, request={"url": f"{self.base}/hot"},
                          parse={"list_selector": ".item", "fields": {"title": "span"}}, policy={},
                          schedule={}, browser={"required": True, "site": "s", "headless": True},
                          tags=[], dir=self.root)
        ctx = FetchContext(browser=self.session(), sites={})
        res = HtmlListAdapter().fetch(spec, ctx)
        self.assertTrue(res.ok, res.error)
        self.assertEqual([i.title for i in res.items], ["热榜A"])


if __name__ == "__main__":
    unittest.main()
