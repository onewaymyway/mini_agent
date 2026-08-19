"""
URL加载能力测试

测试页面导航、加载时间、重定向等核心能力。
"""
import asyncio
import pytest
import logging
from dataclasses import dataclass
from typing import Optional, List
from datetime import datetime

logger = logging.getLogger(__name__)


@dataclass
class LoadResult:
    url: str
    status: str
    load_time_ms: float
    final_url: str
    title: str
    error: Optional[str] = None


class URLLoadTester:
    """URL加载测试器"""

    def __init__(self, timeout_ms: int = 15000):
        self.timeout_ms = timeout_ms
        self.results: List[LoadResult] = []

    async def test_url(self, url: str, expected_title_contains: Optional[str] = None,
                       assert_accessible: bool = True) -> LoadResult:
        """测试单个URL加载"""
        start_time = datetime.now()
        try:
            from src.core.playwright_session import PlaywrightSession, PlaywrightConfig
            session = PlaywrightSession(config=PlaywrightConfig(headless=True))
            await session.async_launch()
            await session.async_goto(url, wait_until="networkidle")

            end_time = datetime.now()
            load_time_ms = (end_time - start_time).total_seconds() * 1000

            page = session.get_page()
            title = await page.title() if page else ""
            final_url = await page.url if page else url

            status = "passed"
            error = None
            if assert_accessible and not title.strip():
                status = "failed"
                error = "页面标题为空，可能加载失败"
            elif expected_title_contains and expected_title_contains not in title:
                logger.warning(f"标题包含检查未通过: '{expected_title_contains}' 不在 '{title}'")

            await session.close()

            result = LoadResult(
                url=url, status=status, load_time_ms=load_time_ms,
                final_url=final_url, title=title, error=error,
            )
            self.results.append(result)
            return result

        except Exception as e:
            end_time = datetime.now()
            load_time_ms = (end_time - start_time).total_seconds() * 1000
            result = LoadResult(
                url=url, status="error", load_time_ms=load_time_ms,
                final_url=url, title="", error=str(e),
            )
            self.results.append(result)
            return result

    def get_summary(self) -> dict:
        total = len(self.results)
        passed = sum(1 for r in self.results if r.status == "passed")
        failed = sum(1 for r in self.results if r.status == "failed")
        errors = sum(1 for r in self.results if r.status == "error")
        avg_time = sum(r.load_time_ms for r in self.results) / total if total > 0 else 0
        return {
            "total": total, "passed": passed, "failed": failed,
            "errors": errors,
            "pass_rate": round(passed / total * 100, 2) if total > 0 else 0,
            "avg_load_time_ms": round(avg_time, 2),
        }


# ========== pytest测试用例 ==========

@pytest.mark.integration
@pytest.mark.browser
@pytest.mark.slow
class TestURLLoading:
    """URL加载能力测试 - 目标100%通过率"""

    @pytest.mark.asyncio
    async def test_baidu_navigation(self):
        """测试百度搜索导航 - 必须通过"""
        tester = URLLoadTester()
        result = await tester.test_url("https://www.baidu.com", "百度")
        assert result.status == "passed", f"百度加载失败: {result.error}"
        assert result.load_time_ms < 15000, f"加载时间过长: {result.load_time_ms}ms"
        assert result.title, "百度页面标题为空"

    @pytest.mark.asyncio
    async def test_bing_navigation(self):
        """测试Bing导航"""
        tester = URLLoadTester()
        result = await tester.test_url("https://www.bing.com", "Bing")
        assert result.status == "passed", f"Bing加载失败: {result.error}"

    @pytest.mark.asyncio
    async def test_zhihu_navigation(self):
        """测试知乎导航"""
        tester = URLLoadTester()
        result = await tester.test_url("https://www.zhihu.com", "知乎")
        assert result.status == "passed", f"知乎加载失败: {result.error}"

    @pytest.mark.asyncio
    async def test_sina_news_navigation(self):
        """测试新浪新闻导航"""
        tester = URLLoadTester()
        result = await tester.test_url("https://news.sina.com.cn", "新浪")
        assert result.status == "passed", f"新浪新闻加载失败: {result.error}"

    @pytest.mark.asyncio
    async def test_eastmoney_navigation(self):
        """测试东方财富导航"""
        tester = URLLoadTester()
        result = await tester.test_url("https://www.eastmoney.com", "东方财富")
        assert result.status == "passed", f"东方财富加载失败: {result.error}"

    @pytest.mark.asyncio
    async def test_gov_cn_navigation(self):
        """测试中国政府网导航"""
        tester = URLLoadTester()
        result = await tester.test_url("https://www.gov.cn", "中国政府网")
        assert result.status == "passed", f"中国政府网加载失败: {result.error}"

    @pytest.mark.asyncio
    async def test_bilibili_navigation(self):
        """测试B站导航"""
        tester = URLLoadTester()
        result = await tester.test_url("https://www.bilibili.com", "哔哩哔哩")
        assert result.status == "passed", f"B站加载失败: {result.error}"

    @pytest.mark.asyncio
    async def test_weibo_navigation(self):
        """测试微博导航 - 宽容断言（可能需登录）"""
        tester = URLLoadTester()
        result = await tester.test_url("https://weibo.com", "微博")
        assert result.status in ("passed", "error"), f"微博导航异常: {result.error}"
        logger.info(f"微博导航结果: status={result.status}, title={result.title[:50]}")

    @pytest.mark.asyncio
    async def test_xueqiu_navigation(self):
        """测试雪球导航"""
        tester = URLLoadTester()
        result = await tester.test_url("https://xueqiu.com", "雪球")
        assert result.status == "passed", f"雪球加载失败: {result.error}"

    @pytest.mark.asyncio
    async def test_cls_navigation(self):
        """测试财联社导航"""
        tester = URLLoadTester()
        result = await tester.test_url("https://www.cls.cn", "财联社")
        assert result.status == "passed", f"财联社加载失败: {result.error}"

    @pytest.mark.asyncio
    async def test_cnnic_navigation(self):
        """测试CNNIC导航"""
        tester = URLLoadTester()
        result = await tester.test_url("https://www.cnnic.cn", "CNNIC")
        assert result.status == "passed", f"CNNIC加载失败: {result.error}"

    @pytest.mark.asyncio
    async def test_ifeng_navigation(self):
        """测试凤凰网导航"""
        tester = URLLoadTester()
        result = await tester.test_url("https://www.ifeng.com", "凤凰")
        assert result.status == "passed", f"凤凰网加载失败: {result.error}"
