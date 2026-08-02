"""
base_test_template.py — browser-cdp 技能测试用例基础模板

本模板提供所有测试用例的通用基类，包含：
- 浏览器实例的 mock setup/teardown
- 常用断言方法
- 测试数据工厂
- 日志记录配置

Usage:
    继承 BaseBrowserTest 类，在 setUp 中配置测试参数，
    在 test_XXX 方法中编写具体测试逻辑。
"""
from __future__ import annotations

import unittest
from unittest.mock import patch, Mock, MagicMock
from pathlib import Path
import sys

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent / ".claude" / "skills" / "browser-cdp"
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

# 导入需要测试的模块
import browser_launch
import browser_nav
import browser_extract
import browser_input
import browser_screenshot
import browser_console


class BaseBrowserTest(unittest.TestCase):
    """browser-cdp 技能测试的基础基类"""

    @classmethod
    def setUpClass(cls):
        """类级别设置（可选）"""
        super().setUpClass()
        cls._setup_logging()

    @classmethod
    def tearDownClass(cls):
        """类级别清理（可选）"""
        super().tearDownClass()

    @staticmethod
    def _setup_logging():
        """配置测试日志"""
        import logging
        logging.basicConfig(level=logging.INFO)
        cls.logger = logging.getLogger(__name__)

    def setUp(self):
        """每个测试前的初始化"""
        # 创建 mock 浏览器实例
        self._mock_browser_instance()
        self._setup_mocks()

    def _mock_browser_instance(self):
        """创建 mock 浏览器对象"""
        self.mock_tab = {
            "id": "test-tab-1",
            "url": "about:blank",
            "title": "Test Page",
            "is_active": True
        }
        self.mock_browser = {
            "tabs": [self.mock_tab],
            "active_tab_id": self.mock_tab["id"]
        }

    def _setup_mocks(self):
        """设置所有必要的 mock"""
        # 这里可以添加更多 mock 设置
        pass

    def assertTabUrlContains(self, tab_id: str, expected_substring: str, msg: str = None):
        """断言 tab URL 包含指定子串"""
        actual_url = self._get_tab_url(tab_id)
        if expected_substring not in actual_url:
            self.fail(f"URL '{actual_url}' does not contain '{expected_substring}'" if msg is None else msg)

    def assertTabTitleContains(self, tab_id: str, expected_substring: str, msg: str = None):
        """断言 tab title 包含指定子串"""
        actual_title = self._get_tab_title(tab_id)
        if expected_substring not in actual_title:
            self.fail(f"Title '{actual_title}' does not contain '{expected_substring}'" if msg is None else msg)

    def _get_tab_url(self, tab_id: str) -> str:
        """获取 tab 的 URL"""
        for tab in self.mock_browser["tabs"]:
            if tab["id"] == tab_id:
                return tab.get("url", "")
        raise ValueError(f"Tab {tab_id} not found")

    def _get_tab_title(self, tab_id: str) -> str:
        """获取 tab 的 title"""
        for tab in self.mock_browser["tabs"]:
            if tab["id"] == tab_id:
                return tab.get("title", "")
        raise ValueError(f"Tab {tab_id} not found")

    def create_mock_page_content(self, html_type: str = "simple") -> str:
        """创建模拟页面内容"""
        if html_type == "simple":
            return "<html><head><title>Test Page</title></head><body><h1>Hello World</h1><p>This is a test page.</p></body></html>"
        elif html_type == "ecommerce":
            return self._create_ecommerce_page()
        elif html_type == "news":
            return self._create_news_page()
        elif html_type == "search":
            return self._create_search_page()
        elif html_type == "social":
            return self._create_social_page()
        elif html_type == "form":
            return self._create_form_page()
        return self.create_mock_page_content("simple")

    def _create_ecommerce_page(self) -> str:
        """创建电商页面模拟内容"""
        return '''
<html>
<head><title>Product - Test Store</title></head>
<body>
  <div class="product">
    <h1>Test Product</h1>
    <span class="price">$99.99</span>
    <button class="add-to-cart">Add to Cart</button>
  </div>
  <div class="reviews">
    <div class="review"><span class="rating">★★★★★</span>Great product!</div>
  </div>
</body>
</html>'''

    def _create_news_page(self) -> str:
        """创建新闻页面模拟内容"""
        return '''
<html>
<head><title>Breaking News - Test Site</title></head>
<body>
  <article class="article">
    <h1>Breaking News Title</h1>
    <div class="content">
      <p>This is the first paragraph of the news article...</p>
      <p>This is the second paragraph with more content...</p>
      <p>And this is the third paragraph...</p>
    </div>
    <div class="pagination">
      <a href="/page/2">Next Page</a>
    </div>
  </article>
</body>
</html>'''

    def _create_search_page(self) -> str:
        """创建搜索结果页面模拟内容"""
        return '''
<html>
<head><title>Search Results</title></head>
<body>
  <form id="search-form">
    <input type="text" name="query" value="test query" />
    <button type="submit">Search</button>
  </form>
  <div class="results">
    <div class="result">
      <h3><a href="/result1">Result 1 Title</a></h3>
      <p class="snippet">This is the snippet for result 1...</p>
    </div>
    <div class="result">
      <h3><a href="/result2">Result 2 Title</a></h3>
      <p class="snippet">This is the snippet for result 2...</p>
    </div>
  </div>
  <div class="pagination">
    <a href="/search?q=test+query&page=2">Page 2</a>
  </div>
</body>
</html>'''

    def _create_social_page(self) -> str:
        """创建社交媒体页面模拟内容"""
        return '''
<html>
<head><title>Social Feed</title></head>
<body>
  <div class="feed">
    <div class="post">
      <p>User1 posted something interesting...</p>
      <button class="like">Like</button>
      <button class="comment">Comment</button>
    </div>
    <div class="post">
      <p>User2 shared an awesome photo!</p>
      <img src="photo.jpg" alt="Shared photo" />
      <button class="like">Like</button>
    </div>
  </div>
  <textarea class="status-input" placeholder="What's on your mind?"></textarea>
  <button class="post-btn">Post</button>
</body>
</html>'''

    def _create_form_page(self) -> str:
        """创建表单页面模拟内容"""
        return '''
<html>
<head><title>Contact Form</title></head>
<body>
  <form id="contact-form">
    <label for="name">Name:</label>
    <input type="text" id="name" name="name" />
    
    <label for="email">Email:</label>
    <input type="email" id="email" name="email" />
    
    <label for="message">Message:</label>
    <textarea id="message" name="message"></textarea>
    
    <button type="submit">Submit</button>
  </form>
</body>
</html>'''

    def tearDown(self):
        """每个测试后的清理"""
        # 清理 mock
        pass


if __name__ == "__main__":
    unittest.main()