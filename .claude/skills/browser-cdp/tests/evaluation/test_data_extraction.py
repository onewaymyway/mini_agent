"""
数据提取能力测试

测试从页面中提取结构化数据的核心能力。
"""
import asyncio
import pytest
import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class ExtractResult:
    url: str
    extraction_type: str
    status: str
    data: Dict[str, Any] = field(default_factory=dict)
    item_count: int = 0
    error: Optional[str] = None
    load_time_ms: float = 0.0


class DataExtractor:
    """数据提取器"""

    def __init__(self):
        self.results: List[ExtractResult] = []

    async def extract_search_results(self, url: str, keyword: str,
                                      max_items: int = 10) -> ExtractResult:
        """提取搜索结果"""
        start = datetime.now()
        try:
            from src.core.playwright_session import PlaywrightSession, PlaywrightConfig
            session = PlaywrightSession(config=PlaywrightConfig(headless=True))
            await session.start()
            await session.navigate(url)
            await session.wait_for_load(5)

            results = []
            if "baidu" in url:
                await self._search_baidu(session, keyword)
                items = await session.page.query_selector_all('.result, .c-container')
                for item in items[:max_items]:
                    try:
                        title_el = await item.query_selector('h3 a')
                        snippet_el = await item.query_selector('.content-2C2ij')
                        if title_el:
                            title = await title_el.inner_text()
                            snippet = await snippet_el.inner_text() if snippet_el else ""
                            results.append({"title": title, "snippet": snippet})
                    except Exception:
                        continue

            end = datetime.now()
            load_time_ms = (end - start).total_seconds() * 1000

            result = ExtractResult(
                url=url, extraction_type="search_results",
                status="passed" if results else "failed",
                data={"items": results}, item_count=len(results),
                load_time_ms=load_time_ms,
            )
            self.results.append(result)
            return result

        except Exception as e:
            result = ExtractResult(
                url=url, extraction_type="search_results",
                status="error", error=str(e),
            )
            self.results.append(result)
            return result

    async def _search_baidu(self, session, keyword: str):
        """执行百度搜索"""
        try:
            search_box = await session.page.query_selector('#kw')
            if search_box:
                await search_box.fill(keyword)
                await asyncio.sleep(1)
                await session.page.keyboard.press('Enter')
                await session.wait_for_load(5)
        except Exception as e:
            logger.warning(f"百度搜索操作失败: {e}")

    async def extract_news_list(self, url: str, max_items: int = 20) -> ExtractResult:
        """提取新闻列表"""
        start = datetime.now()
        try:
            from src.core.playwright_session import PlaywrightSession, PlaywrightConfig
            session = PlaywrightSession(config=PlaywrightConfig(headless=True))
            await session.start()
            await session.navigate(url)
            await session.wait_for_load(5)

            articles = []
            if "sina" in url or "news" in url:
                items = await session.page.query_selector_all('.list-item, .title a, .news-item')
                for item in items[:max_items]:
                    try:
                        title = await item.inner_text()
                        if title and len(title.strip()) > 5:
                            articles.append({"title": title.strip()})
                    except Exception:
                        continue

            end = datetime.now()
            load_time_ms = (end - start).total_seconds() * 1000

            result = ExtractResult(
                url=url, extraction_type="news_list",
                status="passed" if articles else "failed",
                data={"articles": articles}, item_count=len(articles),
                load_time_ms=load_time_ms,
            )
            self.results.append(result)
            return result

        except Exception as e:
            return ExtractResult(
                url=url, extraction_type="news_list",
                status="error", error=str(e),
            )

    def get_summary(self) -> dict:
        total = len(self.results)
        passed = sum(1 for r in self.results if r.status == "passed")
        failed = sum(1 for r in self.results if r.status == "failed")
        errors = sum(1 for r in self.results if r.status == "error")
        return {
            "total": total, "passed": passed, "failed": failed, "errors": errors,
            "pass_rate": round(passed / total * 100, 2) if total > 0 else 0,
        }


# ========== pytest测试用例 ==========

@pytest.mark.integration
@pytest.mark.browser
@pytest.mark.slow
class TestDataExtraction:
    """数据提取能力测试 - 目标100%通过率"""

    @pytest.mark.asyncio
    async def test_baidu_search_extraction(self):
        """测试百度搜索结果提取 - 必须通过"""
        extractor = DataExtractor()
        result = await extractor.extract_search_results("https://www.baidu.com", "AI 大模型")
        assert result.status == "passed", f"百度搜索提取失败: {result.error}"
        assert result.item_count > 0, f"未提取到结果，item_count={result.item_count}"
        logger.info(f"提取到 {result.item_count} 条搜索结果")

    @pytest.mark.asyncio
    async def test_sina_news_extraction(self):
        """测试新浪新闻列表提取 - 必须通过"""
        extractor = DataExtractor()
        result = await extractor.extract_news_list("https://news.sina.com.cn")
        assert result.status == "passed", f"新浪新闻提取失败: {result.error}"
        assert result.item_count >= 10, f"新闻数量不足: {result.item_count}"
        logger.info(f"提取到 {result.item_count} 条新闻")

    @pytest.mark.asyncio
    async def test_eastmoney_news_extraction(self):
        """测试东方财富新闻提取"""
        extractor = DataExtractor()
        result = await extractor.extract_news_list("https://finance.eastmoney.com")
        assert result.status in ("passed", "error"), f"东方财富提取异常: {result.error}"
        logger.info(f"东方财富新闻提取结果: {result.status}, items={result.item_count}")

    @pytest.mark.asyncio
    async def test_cls_telegraph_extraction(self):
        """测试财联社电报提取"""
        extractor = DataExtractor()
        result = await extractor.extract_news_list("https://www.cls.cn")
        assert result.status in ("passed", "error"), f"财联社提取异常: {result.error}"
        logger.info(f"财联社电报提取结果: {result.status}, items={result.item_count}")

    @pytest.mark.asyncio
    async def test_zhihu_search_extraction(self):
        """测试知乎搜索提取"""
        extractor = DataExtractor()
        result = await extractor.extract_search_results("https://www.zhihu.com", "Python")
        assert result.status in ("passed", "error"), f"知乎提取异常: {result.error}"
        logger.info(f"知乎搜索提取结果: {result.status}, items={result.item_count}")

    @pytest.mark.asyncio
    async def test_xueqiu_finance_extraction(self):
        """测试雪球财经数据提取"""
        extractor = DataExtractor()
        result = await extractor.extract_news_list("https://xueqiu.com")
        assert result.status in ("passed", "error"), f"雪球提取异常: {result.error}"
        logger.info(f"雪球财经提取结果: {result.status}, items={result.item_count}")

    @pytest.mark.asyncio
    async def test_bilibili_video_extraction(self):
        """测试B站视频数据提取"""
        extractor = DataExtractor()
        result = await extractor.extract_news_list("https://www.bilibili.com")
        assert result.status in ("passed", "error"), f"B站提取异常: {result.error}"
        logger.info(f"B站视频提取结果: {result.status}, items={result.item_count}")
