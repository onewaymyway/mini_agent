"""真实浏览器的 CDP 附着测试：启动无头 Chromium（带调试端口），抓取本地页面。

环境中没有 Playwright 的 Chromium 时自动跳过。它验证的是真实的事件时序，
这是假对象测试无法覆盖的（曾因用 time.sleep 轮询导致“无法创建后台标签页”）。
"""
import http.server
import socket
import subprocess
import tempfile
import threading
import time
import unittest
from pathlib import Path

from core.browser_session import BrowserSession, cdp_available


def _chromium_path():
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            path = pw.chromium.executable_path
        return path if path and Path(path).exists() else None
    except Exception:  # noqa: BLE001
        return None


class _Page(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        body = b"<html><body><article><p>cdp real test page with enough visible text</p></article></body></html>"
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        return


class CdpRealBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.exe = _chromium_path()
        if cls.exe is None:
            raise unittest.SkipTest("未安装 Playwright 的 Chromium，跳过真实浏览器测试")
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            cls.debug_port = s.getsockname()[1]
        cls.profile = Path(tempfile.mkdtemp(prefix="cdp_real_"))
        cls.proc = subprocess.Popen(
            [cls.exe, f"--user-data-dir={cls.profile}", f"--remote-debugging-port={cls.debug_port}",
             "--remote-debugging-address=127.0.0.1", "--no-first-run", "--headless=new", "--no-sandbox",
             "about:blank"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(60):
            if cdp_available(cls.debug_port):
                break
            time.sleep(0.2)
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Page)
        cls.url = f"http://127.0.0.1:{cls.server.server_address[1]}/p"
        threading.Thread(target=cls.server.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "server", None):
            cls.server.shutdown()
            cls.server.server_close()
        if getattr(cls, "proc", None):
            cls.proc.terminate()
            try:
                cls.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                cls.proc.kill()

    def test_fetch_via_attached_browser_and_browser_survives(self):
        session = BrowserSession(self.profile, self.profile / "login.json", mode="cdp", cdp_port=self.debug_port)
        html = session.fetch_html(self.url, timeout_ms=20000)
        self.assertIn("cdp real test page", html)
        # 抓取结束后断开的只是 CDP 连接，浏览器本身仍在运行
        self.assertIsNone(self.proc.poll())
        self.assertTrue(cdp_available(self.debug_port))


if __name__ == "__main__":
    unittest.main()
