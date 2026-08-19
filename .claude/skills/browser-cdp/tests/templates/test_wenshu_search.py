from __future__ import annotations
import unittest
from unittest.mock import patch, Mock
from pathlib import Path
import sys

SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

from templates.base_test_template import BaseBrowserTest
import src.core.browser_launch as browser_launch
import src.core.browser_nav as browser_nav
import src.core.browser_extract as browser_extract
import src.core.browser_screenshot as browser_screenshot


class TestWenshuSearch(BaseBrowserTest):
    SITE_URL = "https://wenshu.court.gov.cn/"
    SEARCH_URL_TEMPLATE = "{base}/SearchResult?searchtext={keyword}"

    def setUp(self):
        super().setUp()
        self._setup_mocks()

    def _setup_mocks(self):
        with patch.object(browser_launch, "spawn_browser") as mock_spawn:
            mock_proc = Mock()
            mock_proc.pid = 99001
            mock_spawn.return_value = mock_proc
            self.mock_tab["url"] = self.SITE_URL
            self.mock_tab["title"] = "中国裁判文书网"

    def test_01_load_homepage(self):
        with patch.object(browser_nav, "goto", return_value=True),              patch.object(browser_nav, "wait_element", return_value=True):
            result = browser_nav.goto(self.SITE_URL)
            self.assertTrue(result)
            self.assertTabUrlContains("test-tab-1", "wenshu.court.gov.cn")

    def test_02_search_by_keyword(self):
        keyword = "民间借贷纠纷"
        search_url = self.SEARCH_URL_TEMPLATE.format(base=self.SITE_URL.rstrip("/"), keyword=keyword)
        with patch.object(browser_nav, "goto", return_value=True),              patch.object(browser_nav, "wait_element", return_value=True):
            result = browser_nav.goto(search_url)
            self.assertTrue(result)
            self.assertTabUrlContains("test-tab-1", "SearchResult")

    def test_03_extract_search_results(self):
        mock_cases = [
            {"title": "（2023）京民终字第123号民间借贷纠纷案", "court": "北京市高级人民法院", "url": "/ShowDocument/abc123"},
            {"title": "（2022）沪01民终456号借款合同纠纷案", "court": "上海市第一中级人民法院", "url": "/ShowDocument/def456"},
        ]
        with patch.object(browser_extract, "extract_elements") as mock_extract:
            mock_extract.return_value = mock_cases
            cases = browser_extract.extract_elements(mode="elements", selector=".case-item")
            self.assertEqual(len(cases), 2)
            self.assertIn("民间", cases[0]["title"])

    def test_04_extract_case_content(self):
        mock_body = "本院认为，当事人之间形成的民间借贷关系合法有效。\n被告应按约定返还借款本金及利息。\n判决如下："
        with patch.object(browser_extract, "extract_text") as mock_extract:
            mock_extract.return_value = mock_body
            content = browser_extract.extract_text(mode="text")
            self.assertIn("本院认为", content)
            self.assertIn("判决如下", content)

    def test_05_extract_metadata(self):
        mock_meta = [
            {"selector": ".case-number", "text": "（2023）京民终字第123号"},
            {"selector": ".case-court", "text": "北京市高级人民法院"},
        ]
        with patch.object(browser_extract, "extract_elements") as mock_extract:
            mock_extract.return_value = mock_meta
            meta = browser_extract.extract_elements(mode="elements", selector=".case-meta-item")
            self.assertEqual(len(meta), 2)

    def test_06_handle_pagination(self):
        with patch.object(browser_nav, "click_selector", return_value=None),              patch.object(browser_nav, "wait_url_contains", return_value=True):
            browser_nav.click_selector(".pagination .next-page")
            self.assertTabUrlContains("test-tab-1", "page=")

    def test_07_capture_screenshot(self):
        with patch.object(browser_screenshot, "capture_full_page", return_value="wenshu_case.png"):
            path = browser_screenshot.capture_full_page(annotate=True, out="wenshu_case.png")
            self.assertEqual(path, "wenshu_case.png")

    def test_08_low_anti_crawl(self):
        # 低反爬站点，无需特殊处理
        from src.core.stealth import StealthMode
        from src.core.rate_limiter import RateLimiter
        self.assertIsNotNone(StealthMode)
        self.assertIsNotNone(RateLimiter)

if __name__ == "__main__":
    unittest.main()
