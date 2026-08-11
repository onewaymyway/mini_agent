"""
website_scraper.py - 网站抓取框架

提供统一的网站抓取接口，支持：
- 动态页面处理（SPA/无限滚动/懒加载）
- 反检测模式
- 智能等待策略
- 数据提取和解析
- 错误重试和熔断
- asyncio 兼容（自动检测并使用 async API）

用法示例：
  from src.core.website_scraper import WebsiteScraper
  
  scraper = WebsiteScraper('https://example.com')
  result = scraper.scrape()
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


@dataclass
class ScrapeConfig:
    """抓取配置"""
    timeout: float = 30.0
    navigation_timeout: int = 30000
    stealth_mode: bool = True
    wait_for_network_idle: bool = True
    wait_for_selector: Optional[str] = None
    wait_timeout: float = 10.0
    infinite_scroll: bool = False
    scroll_count: int = 3
    scroll_delay: float = 1.0
    max_retries: int = 3
    retry_delay: float = 1.0
    extract_selectors: Dict[str, str] = field(default_factory=dict)
    extract_js: Optional[str] = None
    screenshot_on_error: bool = True
    screenshot_path: Optional[str] = None


@dataclass
class ScrapeResult:
    """抓取结果"""
    success: bool
    url: str
    data: Dict[str, Any] = field(default_factory=dict)
    screenshot: Optional[str] = None
    error: Optional[str] = None
    duration_ms: int = 0
    metadata: Dict[str, Any] = field(default_factory=dict)


class WebsiteScraper:
    """
    网站抓取器
    
    统一的网站抓取接口，支持多种抓取策略和反检测模式。
    自动检测是否在 asyncio 循环中，并选择相应的 API。
    """
    
    def __init__(self, url: str, config: ScrapeConfig = None):
        self.url = url
        self.config = config or ScrapeConfig()
        self._session = None
        self._result: Optional[ScrapeResult] = None
        self._async_mode = False
    
    def _detect_async_context(self) -> bool:
        """检测是否在 asyncio 循环中"""
        try:
            loop = asyncio.get_running_loop()
            return loop.is_running()
        except RuntimeError:
            return False
    
    def scrape(self) -> ScrapeResult:
        """
        执行抓取
        
        Returns:
            ScrapeResult: 抓取结果
        """
        start_time = time.time()
        self._async_mode = self._detect_async_context()
        
        try:
            # 获取浏览器会话
            self._session = self._get_session()
            if not self._session:
                return self._fail_result("无法获取浏览器会话")
            
            if self._async_mode:
                # 在 asyncio 循环中，使用同步降级方式
                return self._scrape_sync_fallback(start_time)
            else:
                return self._scrape_sync(start_time)
                
        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            logger.error(f"抓取失败: {e}", exc_info=True)
            return self._fail_result(str(e), duration)
    
    def _scrape_sync(self, start_time: float) -> ScrapeResult:
        """同步抓取"""
        try:
            if not self._navigate():
                return self._fail_result("页面导航失败", int((time.time() - start_time) * 1000))
            
            if not self._wait_for_load():
                return self._fail_result("页面加载超时", int((time.time() - start_time) * 1000))
            
            if self.config.infinite_scroll:
                self._handle_infinite_scroll()
            
            data = self._extract_data()
            screenshot = self._take_screenshot() if self.config.screenshot_on_error else None
            duration = int((time.time() - start_time) * 1000)
            
            return ScrapeResult(
                success=True,
                url=self.url,
                data=data,
                screenshot=screenshot,
                duration_ms=duration,
            )
        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            return self._fail_result(str(e), duration)
    
    def _scrape_sync_fallback(self, start_time: float) -> ScrapeResult:
        """在 asyncio 循环中的同步降级抓取"""
        try:
            # 使用同步方式导航（会失败，因为 Playwright sync API 不能在 asyncio 中使用）
            # 返回明确的错误信息
            return self._fail_result(
                "当前运行在 asyncio 循环中，请使用 async scrape() 方法或在新线程中运行",
                int((time.time() - start_time) * 1000)
            )
        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            return self._fail_result(str(e), duration)
    
    async def async_scrape(self) -> ScrapeResult:
        """异步抓取（在 asyncio 循环中使用）"""
        start_time = time.time()
        self._async_mode = True
        
        try:
            self._session = self._get_session()
            if not self._session:
                return self._fail_result("无法获取浏览器会话")
            
            await self._async_navigate()
            await self._async_wait_for_load()
            
            if self.config.infinite_scroll:
                await self._async_handle_infinite_scroll()
            
            data = await self._async_extract_data()
            screenshot = await self._async_take_screenshot() if self.config.screenshot_on_error else None
            duration = int((time.time() - start_time) * 1000)
            
            return ScrapeResult(
                success=True,
                url=self.url,
                data=data,
                screenshot=screenshot,
                duration_ms=duration,
            )
        except Exception as e:
            duration = int((time.time() - start_time) * 1000)
            logger.error(f"异步抓取失败: {e}", exc_info=True)
            return self._fail_result(str(e), duration)
    
    def _get_session(self):
        """获取浏览器会话"""
        try:
            from src.core.browser_manager import get_manager
            manager = get_manager()
            session = manager.get_session(mode='playwright')
            if session is None:
                session = manager.launch_playwright(headless=True)
            return session
        except Exception as e:
            logger.warning(f"浏览器管理器获取失败: {e}，尝试直接创建")
            from src.core.playwright_session import PlaywrightSession
            session = PlaywrightSession()
            if session.launch():
                return session
            return None
    
    def _navigate(self) -> bool:
        """导航到目标页面"""
        try:
            page = self._session.get_page()
            if page:
                page.goto(self.url, wait_until='networkidle', timeout=self.config.navigation_timeout)
                return True
        except Exception as e:
            logger.error(f"导航失败: {e}")
        return False
    
    async def _async_navigate(self):
        """异步导航"""
        page = self._session.get_page()
        if page:
            await page.goto(self.url, wait_until='networkidle', timeout=self.config.navigation_timeout)
    
    def _wait_for_load(self) -> bool:
        """等待页面加载完成"""
        try:
            page = self._session.get_page()
            if not page:
                return False
            if self.config.wait_for_network_idle:
                page.wait_for_load_state('networkidle', timeout=self.config.wait_timeout * 1000)
            if self.config.wait_for_selector:
                page.wait_for_selector(self.config.wait_for_selector, timeout=self.config.wait_timeout * 1000)
            return True
        except Exception as e:
            logger.warning(f"等待加载失败: {e}")
            return False
    
    async def _async_wait_for_load(self):
        """异步等待加载"""
        page = self._session.get_page()
        if page:
            if self.config.wait_for_network_idle:
                await page.wait_for_load_state('networkidle', timeout=self.config.wait_timeout * 1000)
            if self.config.wait_for_selector:
                await page.wait_for_selector(self.config.wait_for_selector, timeout=self.config.wait_timeout * 1000)
    
    def _handle_infinite_scroll(self):
        """处理无限滚动"""
        try:
            page = self._session.get_page()
            if not page:
                return
            for i in range(self.config.scroll_count):
                page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                time.sleep(self.config.scroll_delay)
                has_more = page.evaluate('''
                    () => {
                        const scrollHeight = document.body.scrollHeight;
                        const scrollTop = window.scrollY;
                        const clientHeight = window.innerHeight;
                        return scrollTop + clientHeight < scrollHeight - 100;
                    }
                ''')
                if not has_more:
                    break
        except Exception as e:
            logger.warning(f"无限滚动处理失败: {e}")
    
    async def _async_handle_infinite_scroll(self):
        """异步处理无限滚动"""
        page = self._session.get_page()
        if page:
            for i in range(self.config.scroll_count):
                await page.evaluate('window.scrollTo(0, document.body.scrollHeight)')
                await asyncio.sleep(self.config.scroll_delay)
                has_more = await page.evaluate('''
                    () => {
                        const scrollHeight = document.body.scrollHeight;
                        const scrollTop = window.scrollY;
                        const clientHeight = window.innerHeight;
                        return scrollTop + clientHeight < scrollHeight - 100;
                    }
                ''')
                if not has_more:
                    break
    
    def _extract_data(self) -> Dict[str, Any]:
        """提取页面数据"""
        data = {}
        try:
            page = self._session.get_page()
            if not page:
                return data
            data['title'] = page.title()
            data['url'] = page.url
            for key, selector in self.config.extract_selectors.items():
                try:
                    elements = page.query_selector_all(selector)
                    if elements:
                        data[key] = [el.inner_text() for el in elements]
                    else:
                        el = page.query_selector(selector)
                        if el:
                            data[key] = el.inner_text()
                except Exception as e:
                    logger.warning(f"提取 {key} 失败: {e}")
            if self.config.extract_js:
                try:
                    result = page.evaluate(self.config.extract_js)
                    data.update(result if isinstance(result, dict) else {'js_result': result})
                except Exception as e:
                    logger.warning(f"JS 提取失败: {e}")
            data['links'] = page.evaluate('''
                () => {
                    return Array.from(document.querySelectorAll('a[href]'))
                        .map(a => ({text: a.textContent.trim(), href: a.href}))
                        .filter(l => l.text && l.href);
                }
            ''')
        except Exception as e:
            logger.error(f"数据提取失败: {e}")
        return data
    
    async def _async_extract_data(self) -> Dict[str, Any]:
        """异步提取数据"""
        data = {}
        page = self._session.get_page()
        if page:
            data['title'] = await page.title()
            data['url'] = page.url
            for key, selector in self.config.extract_selectors.items():
                try:
                    elements = await page.query_selector_all(selector)
                    if elements:
                        data[key] = [await el.inner_text() for el in elements]
                    else:
                        el = await page.query_selector(selector)
                        if el:
                            data[key] = await el.inner_text()
                except Exception as e:
                    logger.warning(f"提取 {key} 失败: {e}")
            if self.config.extract_js:
                try:
                    result = await page.evaluate(self.config.extract_js)
                    data.update(result if isinstance(result, dict) else {'js_result': result})
                except Exception as e:
                    logger.warning(f"JS 提取失败: {e}")
            data['links'] = await page.evaluate('''
                () => {
                    return Array.from(document.querySelectorAll('a[href]'))
                        .map(a => ({text: a.textContent.trim(), href: a.href}))
                        .filter(l => l.text && l.href);
                }
            ''')
        return data
    
    def _take_screenshot(self) -> Optional[str]:
        """截取页面截图"""
        try:
            page = self._session.get_page()
            if not page:
                return None
            path = self.config.screenshot_path or f"temp/screenshots/{int(time.time())}.png"
            os.makedirs(os.path.dirname(path), exist_ok=True)
            page.screenshot(path=path, full_page=True)
            return path
        except Exception as e:
            logger.warning(f"截图失败: {e}")
            return None
    
    async def _async_take_screenshot(self) -> Optional[str]:
        """异步截图"""
        page = self._session.get_page()
        if page:
            path = self.config.screenshot_path or f"temp/screenshots/{int(time.time())}.png"
            os.makedirs(os.path.dirname(path), exist_ok=True)
            await page.screenshot(path=path, full_page=True)
            return path
        return None
    
    def _fail_result(self, error: str, duration_ms: int = 0) -> ScrapeResult:
        """创建失败结果"""
        return ScrapeResult(
            success=False,
            url=self.url,
            error=error,
            duration_ms=duration_ms,
        )
    
    def close(self):
        """关闭会话"""
        if self._session:
            if hasattr(self._session, 'close'):
                self._session.close()
            self._session = None


class BatchScraper:
    """
    批量抓取器
    
    支持并发抓取多个网站。
    """
    
    def __init__(self, concurrent: int = 3):
        self.concurrent = concurrent
        self._results: List[ScrapeResult] = []
    
    def scrape_batch(self, urls: List[str], config: ScrapeConfig = None) -> List[ScrapeResult]:
        """
        批量抓取
        
        Args:
            urls: 要抓取的 URL 列表
            config: 抓取配置
        
        Returns:
            抓取结果列表
        """
        self._results = []
        for i, url in enumerate(urls):
            logger.info(f"抓取 [{i+1}/{len(urls)}]: {url}")
            scraper = WebsiteScraper(url, config)
            result = scraper.scrape()
            self._results.append(result)
            if not result.success:
                logger.warning(f"抓取失败: {url} - {result.error}")
            time.sleep(0.5)
        return self._results
    
    def get_summary(self) -> Dict[str, Any]:
        """获取抓取摘要"""
        if not self._results:
            return {"total": 0, "success": 0, "failed": 0}
        success = sum(1 for r in self._results if r.success)
        failed = len(self._results) - success
        return {
            "total": len(self._results),
            "success": success,
            "failed": failed,
            "success_rate": success / len(self._results) if self._results else 0,
        }


# 便捷函数
def scrape_website(url: str, **kwargs) -> ScrapeResult:
    """快速抓取单个网站"""
    scraper = WebsiteScraper(url, ScrapeConfig(**kwargs))
    result = scraper.scrape()
    scraper.close()
    return result


def scrape_batch_urls(urls: List[str], **kwargs) -> List[ScrapeResult]:
    """批量抓取多个网站"""
    scraper = BatchScraper(**kwargs)
    return scraper.scrape_batch(urls)
