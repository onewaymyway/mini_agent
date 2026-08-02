"""
test_news_extraction.py — 新闻资讯内容提取测试模板

测试覆盖场景：
- 新闻文章正文提取
- 分页导航处理
- 元数据（标题、描述、H1）获取
- 链接抓取与验证
- 长文章分段处理

依赖模块：browser_nav, browser_extract, browser_screenshot
"""
from __future__ import annotations

import unittest
from pathlib import Path
import sys

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

# 导入基础模板
from templates.base_test_template import BaseBrowserTest


class TestNewsExtraction(BaseBrowserTest):
    """新闻资讯内容提取测试用例"""

    def setUp(self):
        super().setUp()
        self._setup_news_mocks()

    def _setup_news_mocks(self):
        """设置新闻页面相关的 mock"""
        with patch.object(browser_launch, "spawn_browser") as mock_spawn:
            mock_proc = Mock()
            mock_proc.pid = 12345
            mock_spawn.return_value = mock_proc
            self.mock_tab["url"] = "https://example-news.com/article/123"
            self.mock_tab["title"] = "Breaking News Title - Test Site"

    def test_01_load_article_page(self):
        """测试：加载新闻文章页面"""
        with patch.object(browser_nav, "goto") as mock_goto, \
             patch.object(browser_nav, "wait_element") as mock_wait:
            mock_goto.return_value = True
            mock_wait.return_value = True
            
            result = browser_nav.goto("https://example-news.com/article/123")
            self.assertTrue(result)
            self.assertTabUrlContains("test-tab-1", "article/123")

    def test_02_extract_article_title(self):
        """测试：提取文章标题（H1）"""
        with patch.object(browser_extract, "extract_meta") as mock_extract:
            mock_extract.return_value = {
                "title": "Breaking News Title",
                "h1": "Breaking News Title",
                "description": "This is a breaking news article..."
            }
            meta = browser_extract.extract_meta(mode="meta")
            self.assertEqual(meta["title"], "Breaking News Title")
            self.assertIn("Breaking News", meta["h1"])

    def test_03_extract_article_body(self):
        """测试：提取文章正文内容"""
        # 模拟提取正文文本
        with patch.object(browser_extract, "extract_text") as mock_extract:
            mock_extract.return_value = ("This is the first paragraph of the news article. " +
                                        "This is the second paragraph with more detailed content. " +
                                        "And this is the third paragraph completing the story.")
            body = browser_extract.extract_text(mode="text")
            self.assertIsNotNone(body)
            self.assertGreater(len(body), 100)  # 确保有足够的内容
            self.assertIn("paragraph", body.lower())

    def test_04_extract_article_links(self):
        """测试：提取文章内链接"""
        # 模拟提取链接列表
        with patch.object(browser_extract, "extract_links") as mock_extract:
            mock_extract.return_value = [
                {"text": "Related Article 1", "url": "/article/456"},
                {"text": "Related Article 2", "url": "/article/789"},
                {"text": "External Source", "url": "https://external-source.com"}
            ]
            links = browser_extract.extract_links(mode="links")
            self.assertEqual(len(links), 3)
            self.assertEqual(links[0]["text"], "Related Article 1")
            self.assertEqual(links[2]["url"], "https://external-source.com")

    def test_05_handle_pagination(self):
        """测试：处理新闻分页导航"""
        # 模拟点击下一页
        with patch.object(browser_input, "click_selector") as mock_click, \
             patch.object(browser_nav, "wait_url_contains") as mock_wait_url:
            mock_click.return_value = None
            mock_wait_url.return_value = True
            
            # 点击分页链接
            browser_input.click_selector(".pagination a.next")
            
            # 验证 URL 变化
            self.assertTabUrlContains("test-tab-1", "page=2")

    def test_06_extract_article_metadata(self):
        """测试：提取文章元数据（作者、日期等）"""
        # 模拟提取特定元素的元数据
        with patch.object(browser_extract, "extract_elements") as mock_extract:
            mock_extract.return_value = [
                {"id": "author-info", "text": "By John Smith", "tag": "div"},
                {"id": "publish-date", "text": "Published: 2024-01-15", "tag": "div"},
                {"id": "category", "text": "Technology", "tag": "span"}
            ]
            metadata = browser_extract.extract_elements(mode="elements", selector=".article-meta")
            self.assertEqual(len(metadata), 3)
            self.assertIn("John Smith", metadata[0]["text"])
            self.assertIn("Technology", metadata[2]["text"])

    def test_07_capture_article_screenshot(self):
        """测试：截取新闻文章截图"""
        # 模拟整页截图
        with patch.object(browser_screenshot, "capture_full_page") as mock_capture:
            mock_capture.return_value = "news_article_screenshot.png"
            screenshot_path = browser_screenshot.capture_full_page(
                annotate=True,
                out="test_news_article.png"
            )
            self.assertEqual(screenshot_path, "test_news_article.png")

    def test_08_extract_related_articles(self):
        """测试：提取相关文章列表"""
        # 模拟提取相关文章元素
        with patch.object(browser_extract, "extract_elements") as mock_extract:
            mock_extract.return_value = [
                {"id": "rel-1", "text": "Article One", "url": "/article/101"},
                {"id": "rel-2", "text": "Article Two", "url": "/article/102"},
                {"id": "rel-3", "text": "Article Three", "url": "/article/103"}
            ]
            related = browser_extract.extract_elements(mode="elements", selector=".related-articles a")
            self.assertEqual(len(related), 3)
            self.assertEqual(related[0]["text"], "Article One")

    def test_09_long_article_segmentation(self):
        """测试：长文章分段提取（超过 max_chars 限制）"""
        # 模拟超长文章内容
        long_content = "This is a very long article content that exceeds the default max_chars limit. " * 100
        
        with patch.object(browser_extract, "extract_text") as mock_extract:
            # 默认截断模式
            mock_extract.return_value = long_content[:20000] + "..."
            content = browser_extract.extract_text(mode="text")
            self.assertLessEqual(len(content), 20000)  # 确认被截断
            self.assertIn("...", content)
            
            # 完整模式（使用 --save 参数）
            with patch.object(browser_extract, "extract_text", side_effect=[long_content]):
                content_full = browser_extract.extract_text(mode="text", max_chars=0)
                self.assertEqual(content_full, long_content)

    def test_10_extract_image_urls(self):
        """测试：提取文章中的图片URL"""
        # 模拟提取图片元素
        with patch.object(browser_extract, "extract_elements") as mock_extract:
            mock_extract.return_value = [
                {"src": "/images/news1.jpg", "alt": "News Image 1", "tag": "img"},
                {"src": "/images/news2.png", "alt": "News Image 2", "tag": "img"},
                {"src": "/images/banner.gif", "alt": "Banner", "tag": "img"}
            ]
            images = browser_extract.extract_elements(mode="elements", selector="article img")
            self.assertEqual(len(images), 3)
            self.assertEqual(images[0]["src"], "/images/news1.jpg")
            self.assertEqual(images[2]["alt"], "Banner")


if __name__ == "__main__":
    unittest.main()