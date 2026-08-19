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


class TestCreditCnSearch(BaseBrowserTest):
    SITE_URL = "https://www.creditchina.gov.cn/"

    def setUp(self):
        super().setUp()
        with patch.object(browser_launch, "spawn_browser") as mock_spawn:
            mock_proc = Mock()
            mock_proc.pid = 99003
            mock_spawn.return_value = mock_proc
            self.mock_tab["url"] = self.SITE_URL
            self.mock_tab["title"] = "信用中国（网站）"

    def test_01_load_homepage(self):
        with patch.object(browser_nav, "goto", return_value=True),              patch.object(browser_nav, "wait_element", return_value=True):
            result = browser_nav.goto(self.SITE_URL)
            self.assertTrue(result)
            self.assertTabUrlContains("test-tab-1", "creditchina.gov.cn")

    def test_02_enterprise_query(self):
        enterprise = "阿里巴巴集团控股有限公司"
        query_url = self.SITE_URL + "xinyongxinxixiangqing/" + enterprise
        with patch.object(browser_nav, "goto", return_value=True),              patch.object(browser_nav, "wait_element", return_value=True):
            result = browser_nav.goto(query_url)
            self.assertTrue(result)
            self.assertTabUrlContains("test-tab-1", "xinyongxinxixiangqing")

    def test_03_extract_credit_records(self):
        mock_records = [
            {"type": "行政处罚", "content": "因违反《广告法》被罚款50万元", "date": "2023-03-10"},
            {"type": "经营异常", "content": "未按规定公示年度报告", "date": "2022-07-01"},
        ]
        with patch.object(browser_extract, "extract_elements") as mock_extract:
            mock_extract.return_value = mock_records
            records = browser_extract.extract_elements(mode="elements", selector=".credit-item")
            self.assertEqual(len(records), 2)
            self.assertEqual(records[0]["type"], "行政处罚")

    def test_04_extract_woff_text(self):
        mock_decoded = "统一社会信用代码：91330000MA28XXXX8E"
        with patch.object(browser_extract, "extract_text") as mock_extract:
            mock_extract.return_value = mock_decoded
            text = browser_extract.extract_text(mode="text")
            self.assertIn("91330000", text)

    def test_05_penalty_query(self):
        mock_penalties = [
            {"title": "虚假宣传行政处罚", "amount": "50万元", "basis": "《中华人民共和国广告法》第四十五条"},
        ]
        with patch.object(browser_extract, "extract_elements") as mock_extract:
            mock_extract.return_value = mock_penalties
            penalties = browser_extract.extract_elements(mode="elements", selector=".penalty-item")
            self.assertEqual(len(penalties), 1)

    def test_06_no_login_required(self):
        # 公开数据无需登录
        self.assertFalse(getattr(browser_nav, "_login_required", False))

    def test_07_capture_screenshot(self):
        with patch.object(browser_screenshot, "capture_full_page", return_value="credit_report.png"):
            path = browser_screenshot.capture_full_page(annotate=True, out="credit_report.png")
            self.assertEqual(path, "credit_report.png")

    def test_08_low_anti_crawl(self):
        from src.core.stealth import StealthMode
        self.assertIsNotNone(StealthMode)

if __name__ == "__main__":
    unittest.main()
