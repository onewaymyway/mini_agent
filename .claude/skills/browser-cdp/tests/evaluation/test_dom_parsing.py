"""
DOM解析能力测试

测试元素定位、选择器匹配、文本提取等核心能力。
"""
import asyncio
import pytest
import logging
from dataclasses import dataclass
from typing import Optional, List
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class DOMResult:
    url: str
    selector: str
    found: bool
    element_count: int
    text_preview: Optional[str] = None
    error: Optional[str] = None


class DOMTester:
    """DOM解析测试器"""

    def __init__(self):
        self.results: List[DOMResult] = []

    async def test_selector(self, url: str, selector: str,
                            expected_min_count: int = 1) -> DOMResult:
        """测试CSS选择器匹配"""
        try:
            from src.core.playwright_session import PlaywrightSession, PlaywrightConfig
            session = PlaywrightSession(config=PlaywrightConfig(headless=True))
            await session.start()
            await session.navigate(url)
            await session.wait_for_load(5)

            elements = await session.page.query_selector_all(selector)
            count = len(elements)
            found = count >= expected_min_count

            text_preview = None
            if elements:
                try:
                    first_text = await elements[0].inner_text()
                    text_preview = first_text[:100] if first_text else None
                except Exception:
                    pass

            await session.close()

            result = DOMResult(
                url=url, selector=selector, found=found,
                element_count=count, text_preview=text_preview,
            )
            self.results.append(result)
            return result

        except Exception as e:
            result = DOMResult(
                url=url, selector=selector, found=False,
                element_count=0, error=str(e),
            )
            self.results.append(result)
            return result


# ========== pytest测试用例 ==========

@pytest.mark.integration
@pytest.mark.browser
@pytest.mark.slow
class TestDOMParsing:
    """DOM解析能力测试 - 目标100%通过率"""

    @pytest.mark.asyncio
    async def test_baidu_search_box(self):
        """测试百度搜索框定位 - 必须通过"""
        tester = DOMTester()
        result = await tester.test_selector("https://www.baidu.com", '#kw', expected_min_count=1)
        assert result.found, f"百度搜索框未找到: {result.error}"
        assert result.element_count >= 1

    @pytest.mark.asyncio
    async def test_baidu_search_button(self):
        """测试百度搜索按钮定位 - 必须通过"""
        tester = DOMTester()
        result = await tester.test_selector("https://www.baidu.com", '#su', expected_min_count=1)
        assert result.found, f"百度搜索按钮未找到: {result.error}"

    @pytest.mark.asyncio
    async def test_bing_search_box(self):
        """测试Bing搜索框定位"""
        tester = DOMTester()
        result = await tester.test_selector("https://www.bing.com", '#sb_form_q')
        logger.info(f"Bing搜索框: found={result.found}, count={result.element_count}")
        assert result.found, f"Bing搜索框未找到: {result.error}"

    @pytest.mark.asyncio
    async def test_zhihu_nav_items(self):
        """测试知乎导航项定位"""
        tester = DOMTester()
        result = await tester.test_selector("https://www.zhihu.com", '.AppHeader-navItem', expected_min_count=3)
        assert result.found, f"知乎导航项未找到: {result.error}"

    @pytest.mark.asyncio
    async def test_sina_news_list(self):
        """测试新浪新闻列表定位"""
        tester = DOMTester()
        result = await tester.test_selector("https://news.sina.com.cn", '.list-item a', expected_min_count=5)
        assert result.found, f"新浪新闻列表未找到: {result.error}"
        assert result.element_count >= 5

    @pytest.mark.asyncio
    async def test_eastmoney_headlines(self):
        """测试东方财富头条定位"""
        tester = DOMTester()
        result = await tester.test_selector("https://www.eastmoney.com", '.headline-list li a', expected_min_count=3)
        assert result.found, f"东方财富头条未找到: {result.error}"

    @pytest.mark.asyncio
    async def test_bilibili_video_cards(self):
        """测试B站视频卡片定位"""
        tester = DOMTester()
        result = await tester.test_selector("https://www.bilibili.com", '.video-item a', expected_min_count=3)
        assert result.found, f"B站视频卡片未找到: {result.error}"

    @pytest.mark.asyncio
    async def test_weibo_hot_topics(self):
        """测试微博热搜定位"""
        tester = DOMTester()
        result = await tester.test_selector("https://weibo.com", '.hot-search-item')
        assert result.found, f"微博热搜未找到: {result.error}"

    @pytest.mark.asyncio
    async def test_xueqiu_stock_list(self):
        """测试雪球股票列表定位"""
        tester = DOMTester()
        result = await tester.test_selector("https://xueqiu.com", '.stock-item', expected_min_count=3)
        assert result.found, f"雪球股票列表未找到: {result.error}"

    @pytest.mark.asyncio
    async def test_gov_cn_nav(self):
        """测试中国政府网导航定位"""
        tester = DOMTester()
        result = await tester.test_selector("https://www.gov.cn", '.nav-main a', expected_min_count=3)
        assert result.found, f"中国政府网导航未找到: {result.error}"

    @pytest.mark.asyncio
    async def test_cls_telegraph_list(self):
        """测试财联社电报列表定位"""
        tester = DOMTester()
        result = await tester.test_selector("https://www.cls.cn", '.telegraph-list .item', expected_min_count=3)
        assert result.found, f"财联社电报未找到: {result.error}"

    @pytest.mark.asyncio
    async def test_ifeng_nav(self):
        """测试凤凰网导航定位"""
        tester = DOMTester()
        result = await tester.test_selector("https://www.ifeng.com", '.nav a', expected_min_count=5)
        assert result.found, f"凤凰网导航未找到: {result.error}"
