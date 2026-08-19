"""
fault_tolerant_navigator.py - 容错导航器

提供多重恢复策略的安全导航：
- 多重导航策略（networkidle, domcontentloaded, load）
- 自动重试与退避
- 导航中断恢复
- 页面状态验证
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class NavigateResult:
    """导航结果"""
    success: bool
    url: str
    title: str
    status_code: Optional[int] = None
    strategy: str = ""
    elapsed: float = 0.0
    error: Optional[str] = None
    details: Dict[str, Any] = None

    def __post_init__(self):
        if self.details is None:
            self.details = {}


class FaultTolerantNavigator:
    """容错导航器"""

    def __init__(
        self,
        max_retries: int = 3,
        base_timeout: int = 30000,
        backoff_factor: float = 2.0,
        strategies: Optional[List[str]] = None,
    ):
        self.max_retries = max_retries
        self.base_timeout = base_timeout
        self.backoff_factor = backoff_factor
        self.strategies = strategies or [
            "networkidle",
            "domcontentloaded",
            "load",
        ]
        self._history: List[Dict] = []

    async def safe_navigate(
        self,
        page,
        url: str,
        timeout: Optional[int] = None,
        wait_until: Optional[str] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> NavigateResult:
        """
        安全导航，支持多重恢复策略

        Args:
            page: Playwright page 对象
            url: 目标 URL
            timeout: 超时时间（毫秒）
            wait_until: 等待策略
            headers: 请求头

        Returns:
            NavigateResult
        """
        start_time = time.time()
        timeout = timeout or self.base_timeout

        # 尝试多种导航策略
        for attempt in range(self.max_retries):
            strategy_result = None

            for strategy in self.strategies:
                try:
                    strategy_result = await self._navigate_with_strategy(
                        page, url, strategy, timeout, wait_until, headers
                    )
                    if strategy_result.success:
                        result = strategy_result
                        result.strategy = strategy
                        result.elapsed = time.time() - start_time
                        self._record_success(url, strategy, result.elapsed)
                        return result
                except Exception as e:
                    logger.debug(f"Strategy '{strategy}' failed: {e}")
                    continue

            # 所有策略失败，等待后退重试
            if attempt < self.max_retries - 1:
                wait_time = self.base_timeout / 1000 * (self.backoff_factor ** attempt)
                logger.warning(
                    f"Navigation to {url} failed, retrying in {wait_time:.1f}s..."
                )
                await asyncio.sleep(wait_time)

        # 所有重试失败
        result = NavigateResult(
            success=False,
            url=url,
            title="",
            strategy=self.strategies[-1] if self.strategies else "unknown",
            elapsed=time.time() - start_time,
            error="All navigation strategies failed",
        )
        self._record_failure(url, result.elapsed)
        return result

    async def _navigate_with_strategy(
        self,
        page,
        url: str,
        strategy: str,
        timeout: int,
        wait_until: Optional[str],
        headers: Optional[Dict[str, str]],
    ) -> NavigateResult:
        """使用指定策略导航"""
        start = time.time()

        # 构建导航选项
        goto_kwargs = {
            "url": url,
            "timeout": timeout,
            "wait_until": wait_until or strategy,
        }
        if headers:
            goto_kwargs["headers"] = headers

        # 执行导航
        try:
            response = await page.goto(**goto_kwargs)
        except Exception as e:
            # 导航失败，尝试重新加载（P41: 携带原始headers避免反爬）
            try:
                reload_kwargs = {"timeout": timeout, "wait_until": "networkidle"}
                if headers:
                    reload_kwargs["extra_headers"] = headers
                await page.reload(**reload_kwargs)
                response = None
            except Exception as e2:
                raise Exception(f"Navigation and reload both failed: {e2}")

        # 验证页面状态
        is_visible = await page.is_visible("body")
        title = await page.title() if is_visible else ""
        status_code = response.status if response else None

        # 检查页面内容
        content_length = await page.evaluate("document.body ? document.body.innerText.length : 0")

        success = is_visible and content_length > 10

        return NavigateResult(
            success=success,
            url=url,
            title=title,
            status_code=status_code,
            strategy=strategy,
            elapsed=time.time() - start,
            details={
                "content_length": content_length,
                "is_visible": is_visible,
            },
        )

    async def safe_reload(
        self, page, timeout: int = 30000, max_retries: int = 3
    ) -> bool:
        """
        安全重新加载页面

        Returns:
            是否成功
        """
        for attempt in range(max_retries):
            try:
                await page.reload(timeout=timeout, wait_until="networkidle")
                is_visible = await page.is_visible("body")
                if is_visible:
                    return True
            except Exception as e:
                logger.debug(f"Reload attempt {attempt + 1} failed: {e}")
                if attempt < max_retries - 1:
                    await asyncio.sleep(1)
        return False

    async def wait_for_content(
        self,
        page,
        selector: str,
        min_length: int = 10,
        timeout: int = 10000,
    ) -> bool:
        """
        等待内容出现

        Returns:
            是否成功
        """
        start = time.time()
        while time.time() - start < timeout / 1000:
            try:
                el = await page.query_selector(selector)
                if el:
                    text = await el.inner_text()
                    if len(text.strip()) >= min_length:
                        return True
            except:
                pass
            await asyncio.sleep(0.5)
        return False

    async def wait_for_network_idle(
        self, page, timeout: int = 5000
    ) -> bool:
        """
        等待网络空闲

        Returns:
            是否成功
        """
        try:
            await page.wait_for_load_state("networkidle", timeout=timeout)
            return True
        except:
            return False

    async def check_page_stability(
        self, page, check_count: int = 3, interval: float = 1.0
    ) -> bool:
        """
        检查页面稳定性

        Returns:
            页面是否稳定
        """
        try:
            # 获取当前内容哈希
            content1 = await page.evaluate("document.body ? document.body.innerText : ''")
            
            for _ in range(check_count):
                await asyncio.sleep(interval)
                content2 = await page.evaluate("document.body ? document.body.innerText : ''")
                
                if content1 != content2:
                    content1 = content2
                    continue
                
                # 连续多次内容不变，认为页面稳定
                return True
            
            return False
        except:
            return False

    def _record_success(self, url: str, strategy: str, elapsed: float):
        """记录成功导航"""
        self._history.append({
            "url": url,
            "strategy": strategy,
            "elapsed": elapsed,
            "success": True,
            "timestamp": time.time(),
        })
        # 保持历史记录不超过 100 条
        if len(self._history) > 100:
            self._history = self._history[-100:]

    def _record_failure(self, url: str, elapsed: float):
        """记录失败导航"""
        self._history.append({
            "url": url,
            "strategy": "unknown",
            "elapsed": elapsed,
            "success": False,
            "timestamp": time.time(),
        })
        if len(self._history) > 100:
            self._history = self._history[-100:]

    def get_stats(self) -> Dict[str, Any]:
        """获取导航统计"""
        if not self._history:
            return {
                "total": 0,
                "success": 0,
                "failure": 0,
                "success_rate": 0.0,
                "avg_elapsed": 0.0,
            }

        successes = [h for h in self._history if h["success"]]
        failures = [h for h in self._history if not h["success"]]

        return {
            "total": len(self._history),
            "success": len(successes),
            "failure": len(failures),
            "success_rate": len(successes) / len(self._history) if self._history else 0,
            "avg_elapsed": sum(h["elapsed"] for h in self._history) / len(self._history),
        }


class NavigationOrchestrator:
    """导航编排器 - 管理多个页面的导航"""

    def __init__(self, max_concurrent: int = 3):
        self.max_concurrent = max_concurrent
        self.navigator = FaultTolerantNavigator()
        self._results: Dict[str, NavigateResult] = {}
        self._page = None  # P42: 需外部传入page对象

    def set_page(self, page):
        """设置要使用的page对象（P42修复）"""
        self._page = page

    async def navigate_multiple(
        self,
        urls: List[str],
        timeout: int = 30000,
        page=None,
    ) -> Dict[str, NavigateResult]:
        """
        并发导航多个 URL

        Args:
            urls: 要导航的URL列表
            timeout: 单个导航超时（毫秒）
            page: CDP页面对象，若未设置则使用self._page

        Returns:
            {url: NavigateResult}
        """
        target_page = page or self._page
        semaphore = asyncio.Semaphore(self.max_concurrent)
        tasks = {}

        async def _navigate(url: str) -> NavigateResult:
            async with semaphore:
                return await self.navigator.safe_navigate(target_page, url, timeout=timeout)

        for url in urls:
            tasks[url] = asyncio.create_task(_navigate(url))

        results = await asyncio.gather(*tasks.values(), return_exceptions=True)

        for url, result in zip(urls, results):
            if isinstance(result, Exception):
                self._results[url] = NavigateResult(
                    success=False,
                    url=url,
                    title="",
                    error=str(result),
                )
            else:
                self._results[url] = result

        return self._results

    def get_result(self, url: str) -> Optional[NavigateResult]:
        """获取单个 URL 的导航结果"""
        return self._results.get(url)

    def get_summary(self) -> Dict[str, Any]:
        """获取导航摘要"""
        if not self._results:
            return {"total": 0, "success": 0, "failure": 0}

        successes = [r for r in self._results.values() if r.success]
        return {
            "total": len(self._results),
            "success": len(successes),
            "failure": len(self._results) - len(successes),
            "success_rate": len(successes) / len(self._results) if self._results else 0,
        }


# 便捷函数
def create_navigator(**kwargs) -> FaultTolerantNavigator:
    """创建容错导航器"""
    return FaultTolerantNavigator(**kwargs)


async def safe_goto(page, url: str, **kwargs) -> NavigateResult:
    """
    安全导航便捷函数

    Usage:
        result = await safe_goto(page, "https://example.com")
        if result.success:
            print(f"Title: {result.title}")
    """
    navigator = FaultTolerantNavigator()
    return await navigator.safe_navigate(page, url, **kwargs)
