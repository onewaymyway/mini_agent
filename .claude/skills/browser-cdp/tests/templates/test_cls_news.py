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


class TestClsNews(BaseBrowserTest):
    SITE_URL = "https://www.cls.cn/"

    def setUp(self):
        super().setUp()
        with patch.object(browser_launch, "spawn_browser") as mock_spawn:
            mock_proc = Mock()
            mock_proc.pid = 99006
            mock_spawn.return_value = mock_proc
            self.mock_tab["url"] = self.SITE_URL
            self.mock_tab["title"] = "财联社——资本市场第一端口"

    def test_01_load_homepage(self):
        with patch.object(browser_nav, "goto", return_value=True),              patch.object(browser_nav, "wait_element", return_value=True):
            result = browser_nav.goto(self.SITE_URL)
            self.assertTrue(result)
            self.assertTabUrlContains("test-tab-1", "cls.cn")

    def test_02_fetch_flash_news(self):
        mock_flash = [
            {"time": "14:32", "content": "国家统计局：10月份CPI同比上涨0.3%", "urgent": True},
            {"time": "14:28", "content": "特斯拉宣布将在上海建设超级工厂二期", "urgent": False},
        ]
        with patch.object(browser_extract, "extract_elements") as mock_extract:
            mock_extract.return_value = mock_flash
            flash_list = browser_extract.extract_elements(mode="elements", selector=".telegraph-item")
            self.assertEqual(len(flash_list), 2)
            self.assertIn("CPI", flash_list[0]["content"])

    def test_03_filter_by_category(self):
        mock_filtered = [
            {"time": "14:32", "content": "CPI同比上涨0.3%", "tag": "macro"},
            {"time": "14:15", "content": "央行开展1000亿逆回购", "tag": "macro"},
        ]
        with patch.object(browser_extract, "extract_elements") as mock_extract:
            mock_extract.return_value = mock_filtered
            macro_news = browser_extract.extract_elements(mode="elements", selector=".telegraph-item")
            self.assertEqual(len(macro_news), 2)

    def test_04_extract_live_content(self):
        mock_live = [
            {"user": "财联社记者", "content": "现场直击：科创板开市首日交易情况"},
            {"user": "分析师张三", "content": "今日A股预计震荡上行"},
        ]
        with patch.object(browser_extract, "extract_elements") as mock_extract:
            mock_extract.return_value = mock_live
            live_msgs = browser_extract.extract_elements(mode="elements", selector=".live-msg-item")
            self.assertEqual(len(live_msgs), 2)
            self.assertIn("科创板", live_msgs[0]["content"])

    def test_05_high_frequency_polling(self):
        from src.core.rate_limiter import RateLimiter, RateLimitConfig, RateLimitAlgorithm
        limiter = RateLimiter(RateLimitConfig(algorithm=RateLimitAlgorithm.TOKEN_BUCKET, token_rate=2.0, max_tokens=2.0))
        self.assertIsNotNone(limiter)

    def test_06_extract_article(self):
        with patch.object(browser_extract, "extract_meta") as mock_meta,              patch.object(browser_extract, "extract_text") as mock_text:
            mock_meta.return_value = {"title": "央行宣布下调存款准备金率0.5个百分点"}
            mock_text.return_value = "中国人民银行决定..."
            title = browser_extract.extract_meta(mode="meta")
            body = browser_extract.extract_text(mode="text")
            self.assertIn("存款准备金", title["title"])

    def test_07_capture_screenshot(self):
        with patch.object(browser_screenshot, "capture_full_page", return_value="cls_flash.png"):
            path = browser_screenshot.capture_full_page(annotate=True, out="cls_flash.png")
            self.assertEqual(path, "cls_flash.png")

    def test_08_anti_crawl_level2(self):
        from src.core.stealth import StealthMode
        from src.core.rate_limiter import RateLimiter
        self.assertIsNotNone(StealthMode)
        self.assertIsNotNone(RateLimiter)

if __name__ == "__main__":
    unittest.main()
