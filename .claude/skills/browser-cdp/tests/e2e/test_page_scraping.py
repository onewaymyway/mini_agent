"""
test_page_scraping.py - 页面抓取端到端测试

验证 browser_load.py 对 5 类典型页面的抓取能力：
1. 新闻站点（新浪新闻）
2. 电商站点（京东）
3. 搜索站点（百度）
4. 社交站点（知乎）
5. 学术站点（arXiv）

用法：
  pytest tests/e2e/test_page_scraping.py -v
  pytest tests/e2e/test_page_scraping.py -v -k "test_news"
"""
from __future__ import annotations

import pytest
import time
import json
import os
from pathlib import Path
from typing import Dict, Any, List
from unittest.mock import MagicMock, patch

# 添加 skill 根目录到路径
SKILL_ROOT = Path(__file__).parent.parent.parent
import sys
sys.path.insert(0, str(SKILL_ROOT))

from src.core.browser_load import (
    load_page,
    LoadResult,
    detect_page_type,
    PAGE_TYPE_NEWS,
    PAGE_TYPE_ECOMMERCE,
    PAGE_TYPE_SEARCH,
    PAGE_TYPE_SOCIAL,
    PAGE_TYPE_ACADEMIC,
    PAGE_TYPE_GENERAL,
)
from src.core.browser_extract import (
    TEXT_JS,
    LINKS_JS,
    FORMS_JS,
    META_JS,
)


# ============================================================================
# 测试配置
# ============================================================================

# 测试用的典型页面 URL
TEST_PAGES: Dict[str, str] = {
    "news": "https://news.sina.com.cn/",
    "ecommerce": "https://www.jd.com/",
    "search": "https://www.baidu.com/",
    "social": "https://www.zhihu.com/",
    "academic": "https://arxiv.org/",
}

# 输出目录
OUTPUT_DIR = Path(__file__).parent.parent.parent / "output" / "test_scraping"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)


# ============================================================================
# 测试数据夹具
# ============================================================================

@pytest.fixture
def mock_session():
    """创建模拟 CDP session"""
    session = MagicMock()
    session.eval_js.return_value = "Test Title"
    session.send.return_value = {"root": {"nodeId": 1}, "outerHTML": "<html><body>Test</body></html>"}
    return session


@pytest.fixture
def mock_elements():
    """创建模拟元素列表"""
    return [
        {"index": 0, "tag": "a", "text": "新闻链接", "rect": {"x": 10, "y": 20, "width": 100, "height": 30}, "inViewport": True},
        {"index": 1, "tag": "button", "text": "搜索", "rect": {"x": 50, "y": 60, "width": 80, "height": 25}, "inViewport": True},
        {"index": 2, "tag": "input", "text": "", "rect": {"x": 100, "y": 100, "width": 200, "height": 30}, "inViewport": True},
    ]


# ============================================================================
# 页面类型检测测试
# ============================================================================

class TestPageTypeDetection:
    """测试页面类型自动检测"""
    
    def test_detect_news_site(self):
        """测试新闻站点检测"""
        assert detect_page_type("https://news.sina.com.cn/", "新浪新闻") == PAGE_TYPE_NEWS
        assert detect_page_type("https://news.baidu.com/", "百度新闻") == PAGE_TYPE_NEWS
        assert detect_page_type("https://www.thepaper.cn/", "澎湃新闻") == PAGE_TYPE_NEWS
    
    def test_detect_ecommerce_site(self):
        """测试电商站点检测"""
        assert detect_page_type("https://www.jd.com/", "京东") == PAGE_TYPE_ECOMMERCE
        assert detect_page_type("https://www.taobao.com/", "淘宝") == PAGE_TYPE_ECOMMERCE
        assert detect_page_type("https://www.amazon.cn/", "亚马逊中国") == PAGE_TYPE_ECOMMERCE
    
    def test_detect_search_site(self):
        """测试搜索站点检测"""
        assert detect_page_type("https://www.baidu.com/", "百度一下") == PAGE_TYPE_SEARCH
        assert detect_page_type("https://www.bing.com/", "Bing") == PAGE_TYPE_SEARCH
        assert detect_page_type("https://search.google.com/", "Google Search") == PAGE_TYPE_SEARCH
    
    def test_detect_social_site(self):
        """测试社交站点检测"""
        assert detect_page_type("https://www.zhihu.com/", "知乎") == PAGE_TYPE_SOCIAL
        assert detect_page_type("https://weibo.com/", "微博") == PAGE_TYPE_SOCIAL
        assert detect_page_type("https://www.xiaohongshu.com/", "小红书") == PAGE_TYPE_SOCIAL
    
    def test_detect_academic_site(self):
        """测试学术站点检测"""
        assert detect_page_type("https://arxiv.org/", "arXiv") == PAGE_TYPE_ACADEMIC
        assert detect_page_type("https://scholar.google.com/", "Google Scholar") == PAGE_TYPE_ACADEMIC
        assert detect_page_type("https://www.cnki.net/", "中国知网") == PAGE_TYPE_ACADEMIC
    
    def test_detect_general_site(self):
        """测试通用站点检测"""
        assert detect_page_type("https://example.com/", "Example") == PAGE_TYPE_GENERAL
        assert detect_page_type("https://github.com/", "GitHub") == PAGE_TYPE_GENERAL


# ============================================================================
# LoadResult 测试
# ============================================================================

class TestLoadResult:
    """测试 LoadResult 数据类"""
    
    def test_success_result(self):
        """测试成功结果"""
        result = LoadResult(
            success=True,
            url="https://example.com",
            title="Test Page",
            page_type=PAGE_TYPE_GENERAL,
            mode="text",
            data={"content": "Hello World"},
            elapsed=1.5,
        )
        assert result.success is True
        assert result.url == "https://example.com"
        assert result.title == "Test Page"
        assert result.page_type == PAGE_TYPE_GENERAL
        assert result.mode == "text"
        assert result.elapsed == 1.5
    
    def test_error_result(self):
        """测试错误结果"""
        result = LoadResult(
            success=False,
            url="https://example.com",
            title="",
            page_type=PAGE_TYPE_GENERAL,
            mode="text",
            error="Connection timeout",
            elapsed=30.0,
        )
        assert result.success is False
        assert result.error == "Connection timeout"
        assert result.elapsed == 30.0
    
    def test_result_to_dict(self):
        """测试结果转字典"""
        result = LoadResult(
            success=True,
            url="https://example.com",
            title="Test",
            page_type=PAGE_TYPE_NEWS,
            mode="html",
            data={"content": "<html>...</html>"},
            elapsed=2.3,
        )
        d = result.to_dict()
        assert d["success"] is True
        assert d["url"] == "https://example.com"
        assert d["page_type"] == PAGE_TYPE_NEWS
        assert d["mode"] == "html"
        assert d["elapsed"] == 2.3
        assert "timestamp" in d
    
    def test_result_str_success(self):
        """测试成功结果字符串"""
        result = LoadResult(
            success=True,
            url="https://example.com",
            title="Test Page",
            page_type=PAGE_TYPE_GENERAL,
            mode="text",
            elapsed=1.0,
        )
        result_str = str(result)
        assert "[ok]" in result_str
        assert "Test Page" in result_str
    
    def test_result_str_error(self):
        """测试错误结果字符串"""
        result = LoadResult(
            success=False,
            url="https://example.com",
            title="",
            page_type=PAGE_TYPE_GENERAL,
            mode="text",
            error="Timeout",
            elapsed=30.0,
        )
        result_str = str(result)
        assert "[error]" in result_str
        assert "Timeout" in result_str


# ============================================================================
# 内容提取测试
# ============================================================================

class TestContentExtraction:
    """测试内容提取功能"""
    
    def test_text_extraction_js(self):
        """测试文本提取 JS 模板"""
        assert "body.cloneNode" in TEXT_JS
        assert "innerText" in TEXT_JS or "textContent" in TEXT_JS
        assert "script" in TEXT_JS
        assert "style" in TEXT_JS
    
    def test_links_extraction_js(self):
        """测试链接提取 JS 模板"""
        assert "document.querySelectorAll" in LINKS_JS
        assert "a[href]" in LINKS_JS
        assert "innerText" in LINKS_JS
        assert "href" in LINKS_JS
    
    def test_forms_extraction_js(self):
        """测试表单提取 JS 模板"""
        assert "document.forms" in FORMS_JS
        assert "elements" in FORMS_JS
        assert "tagName" in FORMS_JS
        assert "type" in FORMS_JS
    
    def test_meta_extraction_js(self):
        """测试元数据提取 JS 模板"""
        assert "location.href" in META_JS
        assert "document.title" in META_JS
        assert "meta" in META_JS
        assert "h1" in META_JS


# ============================================================================
# 集成测试（需要真实浏览器）
# ============================================================================

@pytest.mark.integration
@pytest.mark.slow
class TestPageScrapingIntegration:
    """页面抓取集成测试（需要真实浏览器连接）"""
    
    @pytest.fixture(scope="class")
    def browser_session(self, request):
        """获取浏览器会话（需要 --browser-session 参数）"""
        from src.core.utils import get_session
        from src.core.cdp_client import list_tabs
        
        # 尝试连接现有浏览器
        tabs = list_tabs()
        if not tabs:
            pytest.skip("没有可用的浏览器会话")
        
        # 使用第一个 tab
        tab_id = tabs[0]["id"]
        
        # 创建 session
        from src.core.cdp_client import connect_tab, find_tab
        target = find_tab(tab_id=tab_id, port=9222)
        session = connect_tab(target, port=9222)
        
        yield session
        
        session.close()
    
    @pytest.mark.parametrize("page_name,url", [
        ("news", TEST_PAGES["news"]),
        ("ecommerce", TEST_PAGES["ecommerce"]),
        ("search", TEST_PAGES["search"]),
        ("social", TEST_PAGES["social"]),
        ("academic", TEST_PAGES["academic"]),
    ])
    def test_scrape_text(self, browser_session, page_name, url):
        """测试文本抓取"""
        result = load_page(
            session=browser_session,
            url=url,
            mode="text",
            timeout=30.0,
            smart_wait=True,
            stealth=True,
        )
        
        assert result.success, f"{page_name} 页面加载失败: {result.error}"
        assert result.title, f"{page_name} 页面标题为空"
        assert result.page_type, f"{page_name} 页面类型检测失败"
        assert result.data.get("content"), f"{page_name} 页面内容为空"
        assert len(result.data["content"]) > 0, f"{page_name} 页面内容为空字符串"
        
        # 保存结果
        output_file = OUTPUT_DIR / f"{page_name}_text.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        
        print(f"\n[ok] {page_name} 文本抓取成功: {result.title}")
    
    @pytest.mark.parametrize("page_name,url", [
        ("news", TEST_PAGES["news"]),
        ("ecommerce", TEST_PAGES["ecommerce"]),
        ("search", TEST_PAGES["search"]),
        ("social", TEST_PAGES["social"]),
        ("academic", TEST_PAGES["academic"]),
    ])
    def test_scrape_links(self, browser_session, page_name, url):
        """测试链接抓取"""
        result = load_page(
            session=browser_session,
            url=url,
            mode="links",
            timeout=30.0,
            smart_wait=True,
            stealth=True,
        )
        
        assert result.success, f"{page_name} 页面加载失败: {result.error}"
        assert isinstance(result.data.get("content"), list), f"{page_name} 链接结果不是列表"
        
        # 保存结果
        output_file = OUTPUT_DIR / f"{page_name}_links.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        
        print(f"\n[ok] {page_name} 链接抓取成功: {len(result.data['content'])} 个链接")
    
    @pytest.mark.parametrize("page_name,url", [
        ("news", TEST_PAGES["news"]),
        ("ecommerce", TEST_PAGES["ecommerce"]),
        ("search", TEST_PAGES["search"]),
        ("social", TEST_PAGES["social"]),
        ("academic", TEST_PAGES["academic"]),
    ])
    def test_scrape_meta(self, browser_session, page_name, url):
        """测试元数据抓取"""
        result = load_page(
            session=browser_session,
            url=url,
            mode="meta",
            timeout=30.0,
            smart_wait=True,
            stealth=True,
        )
        
        assert result.success, f"{page_name} 页面加载失败: {result.error}"
        assert isinstance(result.data.get("content"), dict), f"{page_name} 元数据结果不是字典"
        assert "title" in result.data["content"], f"{page_name} 缺少 title 字段"
        assert "url" in result.data["content"], f"{page_name} 缺少 url 字段"
        
        # 保存结果
        output_file = OUTPUT_DIR / f"{page_name}_meta.json"
        with open(output_file, "w", encoding="utf-8") as f:
            json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
        
        print(f"\n[ok] {page_name} 元数据抓取成功: {result.data['content']['title']}")
    
    @pytest.mark.parametrize("page_name,url", [
        ("news", TEST_PAGES["news"]),
        ("ecommerce", TEST_PAGES["ecommerce"]),
        ("search", TEST_PAGES["search"]),
        ("social", TEST_PAGES["social"]),
        ("academic", TEST_PAGES["academic"]),
    ])
    def test_page_type_detection(self, browser_session, page_name, url):
        """测试页面类型检测准确性"""
        result = load_page(
            session=browser_session,
            url=url,
            mode="meta",
            timeout=30.0,
            smart_wait=True,
            stealth=True,
        )
        
        assert result.success, f"{page_name} 页面加载失败: {result.error}"
        
        # 验证页面类型
        expected_type = {
            "news": PAGE_TYPE_NEWS,
            "ecommerce": PAGE_TYPE_ECOMMERCE,
            "search": PAGE_TYPE_SEARCH,
            "social": PAGE_TYPE_SOCIAL,
            "academic": PAGE_TYPE_ACADEMIC,
        }[page_name]
        
        assert result.page_type == expected_type, f"{page_name} 页面类型检测错误: {result.page_type} != {expected_type}"
        
        print(f"\n[ok] {page_name} 页面类型检测正确: {result.page_type}")


# ============================================================================
# 性能测试
# ============================================================================

class TestScrapingPerformance:
    """测试抓取性能"""
    
    def test_load_page_timing(self, mock_session):
        """测试页面加载耗时"""
        with patch('src.core.browser_load.cmd_goto') as mock_goto:
            with patch('src.core.browser_load.detect_page_type') as mock_detect:
                mock_goto.return_value = {"url": "https://example.com", "title": "Test"}
                mock_detect.return_value = PAGE_TYPE_GENERAL
                
                start = time.time()
                result = load_page(
                    session=mock_session,
                    url="https://example.com",
                    mode="text",
                    timeout=10.0,
                )
                elapsed = time.time() - start
                
                assert result.success
                assert elapsed < 10.0, f"页面加载耗时过长: {elapsed:.2f}s"
                print(f"\n[ok] 页面加载耗时: {elapsed:.2f}s")


# ============================================================================
# 错误处理测试
# ============================================================================

class TestErrorHandling:
    """测试错误处理"""
    
    def test_timeout_error(self, mock_session):
        """测试超时错误"""
        from src.reliability.error import NavigationTimeoutError
        
        with patch('src.core.browser_load.cmd_goto') as mock_goto:
            mock_goto.side_effect = NavigationTimeoutError("https://example.com", 1.0)
            
            result = load_page(
                session=mock_session,
                url="https://example.com",
                mode="text",
                timeout=1.0,
            )
            
            assert result.success is False
            assert "timed out" in result.error.lower() or "timeout" in result.error.lower()
            print(f"\n[ok] 超时错误处理正确: {result.error}")
    
    def test_connection_error(self, mock_session):
        """测试连接错误"""
        from src.reliability.error import CDPConnectionLostError
        
        with patch('src.core.browser_load.cmd_goto') as mock_goto:
            mock_goto.side_effect = CDPConnectionLostError("Connection lost")
            
            result = load_page(
                session=mock_session,
                url="https://example.com",
                mode="text",
                timeout=10.0,
            )
            
            assert result.success is False
            assert "connection" in result.error.lower()
            print(f"\n[ok] 连接错误处理正确: {result.error}")


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])
