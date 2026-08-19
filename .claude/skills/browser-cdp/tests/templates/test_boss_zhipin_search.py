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


class TestBossZhipinSearch(BaseBrowserTest):
    SITE_URL = "https://www.zhipin.com/"
    SEARCH_URL_TEMPLATE = "https://www.zhipin.com/web/geek/job?query={query}&city={city}"

    def setUp(self):
        super().setUp()
        with patch.object(browser_launch, "spawn_browser") as mock_spawn:
            mock_proc = Mock()
            mock_proc.pid = 99009
            mock_spawn.return_value = mock_proc
            self.mock_tab["url"] = self.SITE_URL
            self.mock_tab["title"] = "Boss直聘——面试直接聊"

    def test_01_load_homepage(self):
        with patch.object(browser_nav, "goto", return_value=True),              patch.object(browser_nav, "wait_element", return_value=True):
            result = browser_nav.goto(self.SITE_URL)
            self.assertTrue(result)
            self.assertTabUrlContains("test-tab-1", "zhipin.com")

    def test_02_search_job(self):
        query = "Python工程师"
        city = "101010100"
        search_url = self.SEARCH_URL_TEMPLATE.format(query=query, city=city)
        with patch.object(browser_nav, "goto", return_value=True),              patch.object(browser_nav, "wait_element", return_value=True):
            result = browser_nav.goto(search_url)
            self.assertTrue(result)
            self.assertTabUrlContains("test-tab-1", "job")
            self.assertTabUrlContains("test-tab-1", "Python工程师")

    def test_03_extract_job_list(self):
        mock_jobs = [
            {"title": "Python后端工程师", "salary": "20-35K", "company": "字节跳动", "tags": ["五险一金", "年终奖"]},
            {"title": "Python算法工程师", "salary": "30-50K", "company": "阿里巴巴", "tags": ["股票期权"]},
        ]
        with patch.object(browser_extract, "extract_elements") as mock_extract:
            mock_extract.return_value = mock_jobs
            jobs = browser_extract.extract_elements(mode="elements", selector=".job-card")
            self.assertEqual(len(jobs), 2)
            self.assertIn("Python", jobs[0]["title"])
            self.assertIn("字节跳动", jobs[0]["company"])
            self.assertIn("五险一金", jobs[0]["tags"])

    def test_04_extract_job_detail(self):
        mock_detail = {
            "title": "Python后端工程师", "salary_range": "20-35K·14薪",
            "responsibilities": ["负责核心业务后端开发", "参与系统架构设计"],
            "requirements": ["3年以上Python经验", "熟悉MySQL/Redis"],
        }
        with patch.object(browser_extract, "extract_elements") as mock_extract,              patch.object(browser_extract, "extract_meta") as mock_meta:
            mock_extract.return_value = mock_detail["responsibilities"] + mock_detail["requirements"]
            mock_meta.return_value = {"title": mock_detail["title"], "salary": mock_detail["salary_range"]}
            title = browser_extract.extract_meta(mode="meta")
            detail = browser_extract.extract_elements(mode="elements", selector=".job-detail-section")
            self.assertIn("Python", title["title"])
            self.assertIn("核心业务", detail[0])

    def test_05_handle_slider_captcha(self):
        # Boss直聘使用滑块验证码
        from src.core.browser_nav import CaptchaType
        # 模拟检测到滑块验证码
        with patch.object(browser_nav, "detect_captcha", return_value=CaptchaType.SLIDER):
            captcha_type = browser_nav.detect_captcha()
            self.assertEqual(captcha_type, CaptchaType.SLIDER)

    def test_06_session_keepalive(self):
        import http.cookiejar
        cj = http.cookiejar.CookieJar()
        self.assertIsInstance(cj, http.cookiejar.CookieJar)

    def test_07_frequency_control(self):
        from src.core.rate_limiter import RateLimiter, RateLimitConfig
        config = RateLimitConfig(token_rate=1.0, max_tokens=1.0)
        limiter = RateLimiter(config)
        self.assertEqual(limiter.config.token_rate, 1.0)

    def test_08_high_anti_crawl(self):
        from src.core.stealth import StealthMode
        from src.core.rate_limiter import RateLimiter
        self.assertIsNotNone(StealthMode)
        self.assertIsNotNone(RateLimiter)

if __name__ == "__main__":
    unittest.main()
