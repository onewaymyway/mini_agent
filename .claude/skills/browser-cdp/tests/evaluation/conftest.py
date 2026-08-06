"""
评估测试 fixtures

提供评估测试所需的通用 fixtures：
- mock_browser: 模拟浏览器会话
- mock_cdp_client: 模拟 CDP 客户端
- eval_config: 评估配置
- test_website_configs: 测试网站配置列表
- output_dirs: 输出目录管理
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch, AsyncMock

import pytest

# 添加 skill 目录到路径
SKILL_DIR = Path(__file__).resolve().parent.parent.parent
if str(SKILL_DIR) not in sys.path:
    sys.path.insert(0, str(SKILL_DIR))


@pytest.fixture
def mock_cdp_client():
    """模拟 CDP 客户端"""
    client = MagicMock()
    client.ws = MagicMock()
    client.ws.connected = True
    client._tab_id = "TEST_TAB_001"
    client._client_id = "TEST_CLIENT"
    client._commands = []
    client._events = {}

    def send(method, params=None):
        client._commands.append({"method": method, "params": params})
        responses = {
            "Runtime.evaluate": {"result": {"result": {"type": "boolean", "value": True}}},
            "DOM.getDocument": {"result": {"root": {"nodeId": 1, "children": []}}},
            "DOM.querySelector": {"result": {"nodeId": 2}},
            "DOM.getAttributes": {"result": {"attributes": []}},
            "DOM.resolveNode": {"result": {"backendNodeId": 1, "node": {"nodeName": "DIV"}}},
            "DOM.getBoxModel": {"result": {"model": {"content": [0, 0, 100, 0, 100, 100, 0, 100]}}},
            "Page.captureScreenshot": {"result": {"data": "base64encoded"}},
            "Input.dispatchMouseEvent": {},
            "Input.dispatchKeyEvent": {},
            "Network.enable": {},
            "Network.disable": {},
        }
        return responses.get(method, {"result": {}})

    client.send = send
    client.subscribe = MagicMock()
    return client


@pytest.fixture
def mock_browser_session(mock_cdp_client):
    """模拟浏览器会话"""
    session = MagicMock()
    session.client = mock_cdp_client
    session.is_connected = True
    session.tab_id = "TEST_TAB_001"
    session.url = "about:blank"
    session.title = "Test Page"
    session.screenshot = MagicMock(return_value=b"fake_screenshot_data")
    return session


@pytest.fixture
def eval_config():
    """评估配置"""
    from scripts.eval_config import EVAL_CONFIG
    return EVAL_CONFIG


@pytest.fixture
def test_website_configs():
    """测试网站配置列表（仅 P0 级，用于快速测试）"""
    from scripts.eval_config import WEBSITE_CONFIGS, get_websites_by_priority
    return get_websites_by_priority("P0")


@pytest.fixture
def all_website_configs():
    """所有网站配置"""
    from scripts.eval_config import WEBSITE_CONFIGS
    return WEBSITE_CONFIGS


@pytest.fixture
def temp_output_dir():
    """临时输出目录"""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def mock_website_data():
    """模拟网站数据"""
    return {
        "baidu": {
            "url": "https://www.baidu.com",
            "title": "百度一下，你就知道",
            "search_results": [
                {"title": "百度首页", "url": "https://www.baidu.com", "snippet": "中国最大的搜索引擎"},
                {"title": "百度新闻", "url": "https://news.baidu.com", "snippet": "实时新闻"},
            ],
            "page_load_time": 1.2,
        },
        "bing": {
            "url": "https://www.bing.com",
            "title": "Microsoft Bing 搜索",
            "search_results": [
                {"title": "Bing 首页", "url": "https://www.bing.com", "snippet": "Microsoft 搜索引擎"},
            ],
            "page_load_time": 1.5,
        },
    }


@pytest.fixture
def mock_search_page_html():
    """模拟搜索结果页面 HTML"""
    return """
    <!DOCTYPE html>
    <html>
    <head><title>Search Results</title></head>
    <body>
        <div class="search-results">
            <div class="result" data-id="1">
                <h3><a href="/result/1">Result Title 1</a></h3>
                <p class="snippet">This is the first result snippet...</p>
                <span class="source">Source 1</span>
            </div>
            <div class="result" data-id="2">
                <h3><a href="/result/2">Result Title 2</a></h3>
                <p class="snippet">This is the second result snippet...</p>
                <span class="source">Source 2</span>
            </div>
            <div class="result" data-id="3">
                <h3><a href="/result/3">Result Title 3</a></h3>
                <p class="snippet">This is the third result snippet...</p>
                <span class="source">Source 3</span>
            </div>
        </div>
        <div class="pagination">
            <a href="?page=2">Next Page</a>
        </div>
    </body>
    </html>
    """


@pytest.fixture
def mock_news_page_html():
    """模拟新闻页面 HTML"""
    return """
    <!DOCTYPE html>
    <html>
    <head><title>News Page</title></head>
    <body>
        <div class="news-list">
            <article class="news-item">
                <h2><a href="/news/1">Breaking News Title 1</a></h2>
                <span class="time">2026-08-05 10:00</span>
                <span class="author">Author 1</span>
                <p class="summary">News summary 1...</p>
            </article>
            <article class="news-item">
                <h2><a href="/news/2">Breaking News Title 2</a></h2>
                <span class="time">2026-08-05 09:30</span>
                <span class="author">Author 2</span>
                <p class="summary">News summary 2...</p>
            </article>
        </div>
        <div class="pagination">
            <a href="?page=2">下一页</a>
        </div>
    </body>
    </html>
    """


@pytest.fixture
def mock_article_page_html():
    """模拟文章详情页 HTML"""
    return """
    <!DOCTYPE html>
    <html>
    <head><title>Article Title</title></head>
    <body>
        <article class="article">
            <h1 class="title">Article Full Title</h1>
            <div class="meta">
                <span class="time">2026-08-05 10:00</span>
                <span class="author">Author Name</span>
            </div>
            <div class="content">
                <p>First paragraph of the article...</p>
                <p>Second paragraph with more details...</p>
                <p>Third paragraph concluding the article...</p>
            </div>
            <div class="tags">
                <span class="tag">tag1</span>
                <span class="tag">tag2</span>
            </div>
        </article>
    </body>
    </html>
    """


@pytest.fixture
def mock_screenshot_data():
    """模拟截图数据"""
    return b"fake_screenshot_png_data"


@pytest.fixture
def mock_performance_metrics():
    """模拟性能指标"""
    return {
        "navigationStart": 1000,
        "responseStart": 1200,
        "domContentLoadedEventEnd": 1500,
        "loadEventEnd": 1800,
        "domInteractive": 1400,
    }


@pytest.fixture
def mock_error_scenarios():
    """模拟错误场景"""
    return [
        {"type": "timeout", "message": "Page load timeout after 30s"},
        {"type": "element_not_found", "message": "Selector '.search-box' not found"},
        {"type": "navigation_failed", "message": "Navigation failed: net::ERR_FAILED"},
        {"type": "anti_crawl", "message": "Anti-crawl mechanism detected"},
        {"type": "captcha", "message": "CAPTCHA challenge presented"},
    ]


def pytest_configure(config):
    """注册自定义 pytest marks"""
    config.addinivalue_line("markers", "evaluation: Evaluation framework tests")
    config.addinivalue_line("markers", "mock: Tests using mock browser")
    config.addinivalue_line("markers", "real: Tests requiring real browser")
    config.addinivalue_line("markers", "slow: Slow running tests")
