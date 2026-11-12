"""反馈插件的端到端测试：有头 Chromium 加载扩展，打开一个会改写地址的页面，
验证工具条出现，且打开记录经接收服务写入数据。

需要：Playwright 的 Chromium + 图形环境（Linux 下可用 xvfb-run）。缺任一条件时自动跳过。
"""
import http.server
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from core.settings import load_settings
from core.store import read_jsonl
from core import evaluation
from server.feedback_receiver import load_or_create_token, make_server
from tests.helpers import make_project

EXT = Path(__file__).resolve().parents[1] / "extension" / "infohub-feedback"
PAGE_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>SPA</title></head>
<body><p>测试正文，用于验证插件在地址被改写后仍然可用。</p>
<script>history.replaceState(null, "", location.pathname + location.search);</script></body></html>"""


def _chromium_ready():
    if os.name == "posix" and not os.environ.get("DISPLAY"):
        return "没有图形环境（可用 xvfb-run 运行）"
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            if not pw.chromium.executable_path or not Path(pw.chromium.executable_path).exists():
                return "未安装 Playwright 的 Chromium"
    except Exception as exc:  # noqa: BLE001
        return f"Playwright 不可用：{exc}"
    return None


class ExtensionE2ETests(unittest.TestCase):
    def _run_case(self, *, path_suffix, setup=None, storage_extra=None, after_load=None):
        """起接收服务与本地页面服务，用有头 Chromium 加载扩展并打开页面。
        返回 (页面里是否出现工具条, settings, 扩展 storage 之外的额外结果)。"""
        reason = _chromium_ready()
        if reason:
            self.skipTest(reason)
        from playwright.sync_api import sync_playwright

        root = make_project({"news_a": {"id": "news_a", "name": "新闻", "category": "news", "kind": "feed",
                                        "adapter": "rss", "request": {"url": "https://n/feed"}}})
        settings = load_settings(root)
        token = load_or_create_token(settings)
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        receiver = make_server(settings, port=port)
        threading.Thread(target=receiver.serve_forever, daemon=True).start()

        page_dir = Path(tempfile.mkdtemp(prefix="ihe2e_"))
        (page_dir / "page.html").write_text(PAGE_HTML, encoding="utf-8")

        class H(http.server.SimpleHTTPRequestHandler):
            def __init__(self, *a, **k):
                super().__init__(*a, directory=str(page_dir), **k)

            def log_message(self, *a):
                return

        web = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=web.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{web.server_address[1]}/page.html"
        if setup:
            setup(settings, base)
        url = base + path_suffix
        shown = None
        try:
            with sync_playwright() as pw:
                ctx = pw.chromium.launch_persistent_context(
                    tempfile.mkdtemp(prefix="ihe2e_prof_"), headless=False,
                    args=[f"--disable-extensions-except={EXT}", f"--load-extension={EXT}", "--no-sandbox"])
                try:
                    for _ in range(50):
                        if ctx.service_workers:
                            break
                        time.sleep(0.2)
                    self.assertTrue(ctx.service_workers, "扩展未加载")
                    ctx.service_workers[0].evaluate(
                        "(cfg) => chrome.storage.local.set(cfg)", {"port": port, "token": token, **(storage_extra or {})})
                    page = ctx.new_page()
                    page.goto(url, wait_until="load")
                    time.sleep(3)
                    shown = page.evaluate("() => !!document.getElementById('infohub-feedback-host')")
                    if after_load:
                        after_load(page)
                finally:
                    ctx.close()
        finally:
            receiver.shutdown()
            receiver.server_close()
            web.shutdown()
            web.server_close()
        return shown, settings

    def test_widget_appears_after_url_rewrite_and_open_is_recorded(self):
        shown, settings = self._run_case(path_suffix="#ihk=0123456789abcdef&ihs=news_a&ihv=plain&iht=E2E")
        self.assertTrue(shown)
        rows = list(read_jsonl(evaluation.exposure_path(settings.paths.data_dir)))
        self.assertTrue(any(r.get("kind") == "open" and r.get("mode") == "dedicated" for r in rows))

    def test_widget_appears_when_fragment_is_completely_lost(self):
        """地址里没有任何 #ihk 片段（站点重定向丢掉了它），靠看板登记的上下文识别。"""
        from core import pending_open

        def register(settings, base):
            pending_open.register(settings.paths.state_dir, url=base + "?token=old", item_key="0123456789abcdef",
                                  source_id="news_a", view="plain", title="E2E")

        shown, _ = self._run_case(path_suffix="?token=new", setup=register)
        self.assertTrue(shown)

    def test_unregistered_page_has_no_widget_by_default(self):
        shown, _ = self._run_case(path_suffix="?x=1")
        self.assertFalse(shown)

    def test_show_all_option_shows_widget_on_unregistered_page(self):
        shown, _ = self._run_case(path_suffix="?x=1", storage_extra={"showAll": True})
        self.assertTrue(shown)


if __name__ == "__main__":
    unittest.main()
