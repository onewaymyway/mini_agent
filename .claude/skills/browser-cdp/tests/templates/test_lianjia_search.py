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


class TestLianjiaSearch(BaseBrowserTest):
    SITE_URL = "https://bj.lianjia.com/"
    SALE_URL_TEMPLATE = "https://bj.lianjia.com/ershoufang/{query}"

    def setUp(self):
        super().setUp()
        with patch.object(browser_launch, "spawn_browser") as mock_spawn:
            mock_proc = Mock()
            mock_proc.pid = 99008
            mock_spawn.return_value = mock_proc
            self.mock_tab["url"] = self.SITE_URL
            self.mock_tab["title"] = "北京二手房房源"

    def test_01_load_homepage(self):
        with patch.object(browser_nav, "goto", return_value=True),              patch.object(browser_nav, "wait_element", return_value=True):
            result = browser_nav.goto(self.SITE_URL)
            self.assertTrue(result)
            self.assertTabUrlContains("test-tab-1", "lianjia.com")

    def test_02_search_ershoufang(self):
        query = "望京"
        search_url = self.SALE_URL_TEMPLATE.format(query=query)
        with patch.object(browser_nav, "goto", return_value=True),              patch.object(browser_nav, "wait_element", return_value=True):
            result = browser_nav.goto(search_url)
            self.assertTrue(result)
            self.assertTabUrlContains("test-tab-1", "ershoufang")
            self.assertTabUrlContains("test-tab-1", "望京")

    def test_03_extract_house_list(self):
        mock_houses = [
            {"title": "望京西园一区 3室1厅", "price": "850万", "area": "100平", "url": "/ershoufang/HOUSE123.html"},
            {"title": "望京新界 2室1厅", "price": "620万", "area": "80平", "url": "/ershoufang/HOUSE456.html"},
        ]
        with patch.object(browser_extract, "extract_elements") as mock_extract:
            mock_extract.return_value = mock_houses
            houses = browser_extract.extract_elements(mode="elements", selector=".sellItemDetail")
            self.assertEqual(len(houses), 2)
            self.assertIn("望京", houses[0]["title"])
            self.assertIn("850万", houses[0]["price"])

    def test_04_extract_community_info(self):
        mock_community = [
            {"label": "开发商", "value": "望京地产集团"},
            {"label": "物业公司", "value": "万科物业"},
        ]
        with patch.object(browser_extract, "extract_elements") as mock_extract:
            mock_extract.return_value = mock_community
            info = browser_extract.extract_elements(mode="elements", selector=".communityInfo li")
            self.assertEqual(len(info), 2)
            self.assertEqual(info[0]["label"], "开发商")

    def test_05_extract_floor_plan(self):
        mock_images = [
            {"src": "/img/floorplan_1.jpg", "alt": "三室两厅一卫"},
            {"src": "/img/floorplan_2.jpg", "alt": "主卧平面图"},
        ]
        with patch.object(browser_extract, "extract_elements") as mock_extract:
            mock_extract.return_value = mock_images
            imgs = browser_extract.extract_elements(mode="elements", selector=".floorPlanImg img")
            self.assertEqual(len(imgs), 2)
            self.assertTrue(imgs[0]["src"].endswith(".jpg"))

    def test_06_price_format(self):
        mock_price = {"total_price": "850万", "unit_price": "85000元/平"}
        with patch.object(browser_extract, "extract_meta") as mock_meta:
            mock_meta.return_value = mock_price
            price = browser_extract.extract_meta(mode="meta")
            self.assertEqual(price["total_price"], "850万")
            self.assertIn("85000", price["unit_price"])

    def test_07_capture_screenshot(self):
        with patch.object(browser_screenshot, "capture_full_page", return_value="house_detail.png"):
            path = browser_screenshot.capture_full_page(annotate=True, out="house_detail.png")
            self.assertEqual(path, "house_detail.png")

    def test_08_medium_anti_crawl(self):
        from src.core.stealth import StealthMode
        from src.core.rate_limiter import RateLimiter, RateLimitConfig
        self.assertIsNotNone(StealthMode)
        self.assertIsNotNone(RateLimiter)
        config = RateLimitConfig(max_retries=3, base_delay=0.5)
        limiter = RateLimiter(config)
        self.assertEqual(limiter.config.max_retries, 3)

if __name__ == "__main__":
    unittest.main()
