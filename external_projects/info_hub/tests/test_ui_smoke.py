"""用真实无头 Chromium 打开看板，检查页面能渲染、标签切换正常、没有 JS 报错。
Chromium 不可用时自动跳过。"""
import threading
import unittest

from core.settings import load_settings
from server.app import make_server
from tests.helpers import make_project
from tests.test_api_and_server import build_project


class UiSmokeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                b = p.chromium.launch(headless=True)
                b.close()
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"Chromium 不可用: {exc}")
        cls.root = build_project()
        cls.httpd = make_server(load_settings(cls.root), port=0)
        cls.url = f"http://127.0.0.1:{cls.httpd.server_address[1]}/"
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def test_dashboard_loads_tabs_and_has_no_js_errors(self):
        from playwright.sync_api import sync_playwright
        errors = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(self.url)
            page.wait_for_selector("#tab-reports:not([hidden])")
            page.click("button[data-tab=sources]")
            page.wait_for_selector("#source-rows tr")
            self.assertIn("新闻A", page.inner_text("#source-rows"))
            page.click("button[data-tab=runs]")
            page.wait_for_selector("#run-rows")
            page.click("button[data-tab=login]")
            page.wait_for_selector("#login-rows")
            # 播放器在无可朗读内容时应保持隐藏或给出提示，不应报错
            browser.close()
        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()


class AddSourceUiTests(UiSmokeTests):
    """P7 添加面板：能展开、切换表单/对话、显示 provider 状态。"""

    def test_add_panel_renders_and_shows_provider_status(self):
        from playwright.sync_api import sync_playwright
        errors = []
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.on("pageerror", lambda e: errors.append(str(e)))
            page.goto(self.url)
            page.click("button[data-tab=sources]")
            page.wait_for_selector("#source-rows tr")
            page.click("#add-panel > summary")
            page.wait_for_selector("#f-name")
            page.wait_for_function("document.querySelector('#llm-status').textContent.length > 0")
            status = page.inner_text("#llm-status")
            self.assertIn("对话生成使用的模型配置", status)
            page.select_option("#f-adapter", "html_list")
            self.assertTrue(page.is_visible("#f-list"))
            self.assertFalse(page.is_visible("#f-items"))
            page.click("button[data-add=dialog]")
            self.assertTrue(page.is_visible("#d-url"))
            browser.close()
        self.assertEqual(errors, [])
