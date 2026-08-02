"""
test_search_engine.py — 搜索引擎测试模板

测试覆盖场景：
- 搜索查询构建与提交
- 搜索结果页解析
- 分页导航处理
- 自动补全功能
- 高级搜索参数验证

依赖模块：browser_nav, browser_extract, browser_input, browser_console
"""
from __future__ import annotations

import unittest
from unittest.mock import patch, Mock
from pathlib import Path
import sys

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))

# 导入基础模板
from templates.base_test_template import BaseBrowserTest


class TestSearchEngine(BaseBrowserTest):
    """搜索引擎测试用例"""

    def setUp(self):
        super().setUp()
        self._setup_search_mocks()

    def _setup_search_mocks(self):
        """设置搜索引擎页面相关的 mock"""
        with patch.object(browser_launch, "spawn_browser") as mock_spawn:
            mock_proc = Mock()
            mock_proc.pid = 12345
            mock_spawn.return_value = mock_proc
            self.mock_tab["url"] = "https://example-search.com/"
            self.mock_tab["title"] = "Search Engine - Home"

    def test_01_load_search_home(self):
        """测试：加载搜索引擎首页"""
        with patch.object(browser_nav, "goto") as mock_goto:
            mock_goto.return_value = True
            result = browser_nav.goto("https://example-search.com/")
            self.assertTrue(result)
            self.assertTabUrlContains("test-tab-1", "example-search.com")
            self.assertTabTitleContains("test-tab-1", "Search Engine")

    def test_02_build_search_query(self):
        """测试：构建搜索查询并输入关键词"""
        # 模拟在搜索框中输入关键词
        with patch.object(browser_input, "type_selector") as mock_type:
            mock_type.return_value = None
            browser_input.type_selector("#search-input", "machine learning tutorial")
            
            # 验证输入内容
            with patch.object(browser_input, "get_value") as mock_get:
                mock_get.return_value = "machine learning tutorial"
                value = browser_input.get_value("#search-input")
                self.assertEqual(value, "machine learning tutorial")

    def test_03_submit_search_query(self):
        """测试：提交搜索查询"""
        # 模拟点击搜索按钮
        with patch.object(browser_input, "click_selector") as mock_click, \
             patch.object(browser_nav, "wait_url_contains") as mock_wait_url:
            mock_click.return_value = None
            mock_wait_url.return_value = True

            browser_input.click_selector("#search-button")
            
            # 验证搜索请求已提交（URL包含查询参数）
            self.assertTabUrlContains("test-tab-1", "q=machine+learning+tutorial")

    def test_04_parse_search_results(self):
        """测试：解析搜索结果列表"""
        # 模拟提取搜索结果元素
        with patch.object(browser_extract, "extract_elements") as mock_extract:
            mock_extract.return_value = [
                {
                    "id": "res-1",
                    "title": "Machine Learning Tutorial - Part 1",
                    "url": "/tutorial/1",
                    "snippet": "Learn the basics of machine learning..."
                },
                {
                    "id": "res-2",
                    "title": "Advanced Machine Learning Techniques",
                    "url": "/tutorial/2",
                    "snippet": "Deep dive into advanced ML algorithms..."
                },
                {
                    "id": "res-3",
                    "title": "Machine Learning Course on YouTube",
                    "url": "https://youtube.com/ml-course",
                    "snippet": "Free video course covering ML fundamentals..."
                }
            ]
            results = browser_extract.extract_elements(mode="elements", selector=".result")
            self.assertEqual(len(results), 3)
            self.assertEqual(results[0]["title"], "Machine Learning Tutorial - Part 1")
            self.assertEqual(results[2]["url"], "https://youtube.com/ml-course")

    def test_05_extract_result_snippets(self):
        """测试：提取搜索结果摘要（snippet）"""
        # 模拟提取 snippet 文本
        with patch.object(browser_extract, "extract_text") as mock_extract:
            mock_extract.return_value = ("This is a comprehensive guide to machine learning. " +
                                        "We cover supervised and unsupervised learning, " +
                                        "neural networks, and practical applications.")
            snippets = browser_extract.extract_text(mode="text", selector=".snippet")
            self.assertIsNotNone(snippets)
            self.assertIn("machine learning", snippets.lower())

    def test_06_handle_pagination(self):
        """测试：处理搜索结果分页"""
        # 模拟翻页操作
        with patch.object(browser_input, "click_selector") as mock_click, \
             patch.object(browser_nav, "wait_url_contains") as mock_wait_url:
            mock_click.return_value = None
            mock_wait_url.return_value = True

            # 点击第2页
            browser_input.click_selector(".pagination a[page=2]")
            self.assertTabUrlContains("test-tab-1", "page=2")
            
            # 点击最后一页
            browser_input.click_selector(".pagination a[last-page]")
            self.assertTabUrlContains("test-tab-1", "page=10")

    def test_08_test_autocomplete(self):
        """测试：搜索自动补全功能"""
        # 模拟触发自动补全建议
        with patch.object(browser_input, "type") as mock_type, \
             patch.object(browser_extract, "extract_elements") as mock_suggestions:
            mock_type.return_value = None
            mock_suggestions.return_value = [
                {"id": "sugg-1", "text": "machine learning courses", "action": "suggest"},
                {"id": "sugg-2", "text": "machine learning algorithms", "action": "suggest"},
                {"id": "sugg-3", "text": "machine learning python", "action": "suggest"}
            ]
            
            # 输入部分关键词
            browser_input.type("#search-input", "machine le")
            
            # 获取建议
            suggestions = browser_extract.extract_elements(mode="elements", selector=".autocomplete-suggestion")
            self.assertEqual(len(suggestions), 3)
            self.assertIn("machine learning", suggestions[0]["text"].lower())

    def test_09_advanced_search_parameters(self):
        """测试：高级搜索参数（时间范围、文件类型等）"""
        # 模拟设置搜索过滤器
        with patch.object(browser_input, "select") as mock_select, \
             patch.object(browser_input, "click_selector") as mock_click:
            mock_select.return_value = None
            mock_click.return_value = None
            
            # 选择时间范围（最近一年）
            browser_input.select("#time-range", "past-year")
            
            # 选择文件类型（PDF）
            browser_input.select("#file-type", "pdf")
            
            # 应用过滤器
            browser_input.click_selector("#apply-filters")
            
            # 验证参数已应用到URL
            self.assertTabUrlContains("test-tab-1", "tbs=qdr:y")
            self.assertTabUrlContains("test-tab-1", "as_filetype=pdf")

    def test_10_search_with_site_filter(self):
        """测试：site: 限定搜索特定网站"""
        # 模拟 site: 搜索
        with patch.object(browser_input, "type_selector") as mock_type:
            mock_type.return_value = None
            browser_input.type_selector("#search-input", "site:github.com machine learning")
            
            # 验证 site: 参数在 URL 中
            with patch.object(browser_nav, "get_url") as mock_get_url:
                mock_get_url.return_value = "https://example-search.com/search?q=site%3Agithub.com+machine+learning"
                url = browser_nav.get_url()
                self.assertIn("site:github.com", url)

    def test_11_extract_search_metadata(self):
        """测试：提取搜索结果元数据（结果数量、耗时等）"""
        # 模拟提取搜索结果统计信息
        with patch.object(browser_extract, "extract_text") as mock_extract:
            mock_extract.return_value = "About 1,234,567 results (0.45 seconds)"
            meta = browser_extract.extract_text(mode="text", selector=".search-stats")
            self.assertIsNotNone(meta)
            self.assertIn("results", meta)
            self.assertIn("0.45", meta)  # 包含耗时信息

    def test_12_clear_search_query(self):
        """测试：清除搜索查询"""
        # 模拟清空搜索框
        with patch.object(browser_input, "clear") as mock_clear, \
             patch.object(browser_input, "type_selector") as mock_type:
            mock_clear.return_value = None
            mock_type.return_value = None
            
            browser_input.clear("#search-input")
            browser_input.type_selector("#search-input", "")
            
            # 验证搜索框为空
            with patch.object(browser_input, "get_value") as mock_get:
                mock_get.return_value = ""
                value = browser_input.get_value("#search-input")
                self.assertEqual(value, "")


if __name__ == "__main__":
    unittest.main()