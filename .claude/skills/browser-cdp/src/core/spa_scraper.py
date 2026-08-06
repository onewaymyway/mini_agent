"""
spa_scraper.py - SPA 页面抓取框架

提供高层 API 用于抓取 SPA 页面，整合：
1. 智能等待（SmartWait）
2. SPA 框架检测（SPADetector）
3. 无限滚动加载（EnhancedDynamicLoader）
4. DOM 变化监听（DOMObserver）
5. 弹窗处理

用法示例：
    from src.core.spa_scraper import SPAScraper
    
    scraper = SPAScraper(session)
    
    # 抓取单个页面
    result = await scraper.scrape(
        url="https://example.com",
        selectors=[".item", ".title"],
        scroll_to_load=True,
    )
    
    # 抓取搜索结果（带分页）
    results = await scraper.scrape_search(
        search_url="https://example.com/search?q={query}",
        item_selector=".result-item",
        max_pages=5,
    )
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union

from src.core.smart_wait import SmartWait, WaitConfig
from src.core.spa_detector import SPADetector, SPAFramework
from src.core.enhanced_dynamic_loader import EnhancedDynamicLoader, ScrollConfig, ScrollResult
from src.core.dom_observer import DOMObserver
from src.core.dynamic_page_support import DynamicPageSupport

logger = logging.getLogger(__name__)


@dataclass
class ScrapedItem:
    """抓取的单个项目"""
    index: int
    selector: str
    text: str
    href: Optional[str] = None
    attributes: Dict[str, str] = field(default_factory=dict)
    rect: Optional[Dict[str, int]] = None
    visible: bool = True
    
    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "selector": self.selector,
            "text": self.text,
            "href": self.href,
            "attributes": self.attributes,
            "rect": self.rect,
            "visible": self.visible,
        }


@dataclass
class ScrapeResult:
    """抓取结果"""
    success: bool
    url: str
    framework: Optional[str] = None
    items: List[ScrapedItem] = field(default_factory=list)
    scroll_pages: int = 0
    scroll_items: int = 0
    wait_time: float = 0.0
    scroll_time: float = 0.0
    extract_time: float = 0.0
    error: Optional[str] = None
    raw_html: Optional[str] = None
    
    def to_dict(self) -> dict:
        return {
            "success": self.success,
            "url": self.url,
            "framework": self.framework,
            "items_count": len(self.items),
            "scroll_pages": self.scroll_pages,
            "scroll_items": self.scroll_items,
            "wait_time": round(self.wait_time, 2),
            "scroll_time": round(self.scroll_time, 2),
            "extract_time": round(self.extract_time, 2),
            "total_time": round(self.wait_time + self.scroll_time + self.extract_time, 2),
            "error": self.error,
            "items": [item.to_dict() for item in self.items],
        }
    
    def save_to_file(self, path: str) -> str:
        """保存结果到文件"""
        import os
        os.makedirs(os.path.dirname(path) if os.path.dirname(path) else ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)
        logger.info(f"结果已保存到: {path}")
        return path


class SPAScraper:
    """
    SPA 页面抓取器
    
    整合智能等待、SPA 检测、无限滚动、内容提取等能力
    """
    
    def __init__(self, session, config: Optional[Dict[str, Any]] = None):
        """
        Args:
            session: CDP session 对象
            config: 配置字典，支持以下键：
                - wait_timeout: 等待超时（秒），默认 30
                - scroll_max_pages: 最大滚动页数，默认 10
                - scroll_delay: 滚动间隔（秒），默认 0.8
                - extract_timeout: 提取超时（秒），默认 10
                - stealth: 是否启用反检测，默认 True
        """
        self.session = session
        self.config = config or {}
        
        # 初始化各组件
        self._smart_wait = SmartWait(session, WaitConfig(
            timeout=self.config.get("wait_timeout", 30.0),
        ))
        self._spa_detector = SPADetector(session)
        self._dynamic_loader = EnhancedDynamicLoader(session, ScrollConfig(
            max_pages=self.config.get("scroll_max_pages", 10),
            scroll_delay=self.config.get("scroll_delay", 0.8),
        ))
        self._dom_observer = DOMObserver(session)
        self._dynamic_support = DynamicPageSupport(session)
        
        # 统计信息
        self._stats = {
            "total_scrapes": 0,
            "success_count": 0,
            "failure_count": 0,
            "avg_wait_time": 0.0,
            "avg_scroll_time": 0.0,
            "avg_extract_time": 0.0,
        }
    
    # =========================================================================
    # 核心抓取方法
    # =========================================================================
    
    async def scrape(
        self,
        url: str,
        selectors: Optional[List[str]] = None,
        scroll_to_load: bool = False,
        wait_for: Optional[str] = None,
        extract_js: Optional[str] = None,
        save_path: Optional[str] = None,
        **kwargs,
    ) -> ScrapeResult:
        """
        抓取 SPA 页面
        
        Args:
            url: 目标 URL
            selectors: CSS 选择器列表，用于提取内容
            scroll_to_load: 是否滚动加载更多内容
            wait_for: 等待策略（networkidle/selector/stable/adaptive）
            extract_js: 自定义 JS 提取脚本
            save_path: 结果保存路径
            **kwargs: 其他参数（max_pages, item_selector 等）
        
        Returns:
            ScrapeResult: 抓取结果
        """
        start_time = time.time()
        self._stats["total_scrapes"] += 1
        
        logger.info(f"开始抓取 SPA 页面: {url}")
        
        try:
            # 1. 导航到页面
            result = await self._navigate(url, wait_for=wait_for)
            if not result["success"]:
                return ScrapeResult(
                    success=False,
                    url=url,
                    error=f"导航失败: {result.get('error')}",
                )
            
            # 2. 检测 SPA 框架
            framework = await self._detect_framework()
            
            # 3. 滚动加载（可选）
            scroll_result = None
            if scroll_to_load:
                scroll_result = await self._scroll_to_load(
                    item_selector=kwargs.get("item_selector", ""),
                    max_pages=kwargs.get("max_pages", self.config.get("scroll_max_pages", 10)),
                )
            
            # 4. 提取内容
            items = await self._extract_content(
                selectors=selectors,
                extract_js=extract_js,
            )
            
            # 5. 组装结果
            elapsed = time.time() - start_time
            scrape_result = ScrapeResult(
                success=True,
                url=url,
                framework=framework.value if framework else None,
                items=items,
                scroll_pages=scroll_result.pages_loaded if scroll_result else 0,
                scroll_items=scroll_result.items_found if scroll_result else 0,
                wait_time=result.get("elapsed", 0),
                scroll_time=scroll_result.total_time if scroll_result else 0,
                extract_time=elapsed - result.get("elapsed", 0) - (scroll_result.total_time if scroll_result else 0),
            )
            
            # 6. 保存结果（可选）
            if save_path:
                scrape_result.save_to_file(save_path)
            
            # 7. 更新统计
            self._update_stats(scrape_result)
            
            logger.info(f"抓取完成: {len(items)} 项，耗时 {elapsed:.2f}s")
            return scrape_result
            
        except Exception as e:
            logger.error(f"抓取失败: {e}", exc_info=True)
            self._stats["failure_count"] += 1
            return ScrapeResult(
                success=False,
                url=url,
                error=str(e),
            )
    
    async def scrape_search(
        self,
        search_url: str,
        query: str,
        item_selector: str,
        max_pages: int = 5,
        **kwargs,
    ) -> List[ScrapeResult]:
        """
        抓取搜索结果（带分页）
        
        Args:
            search_url: 搜索 URL 模板（支持 {query} 占位符）
            query: 搜索关键词
            item_selector: 列表项选择器
            max_pages: 最大页数
            **kwargs: 其他参数传递给 scrape()
        
        Returns:
            List[ScrapeResult]: 每页的抓取结果
        """
        results = []
        current_url = search_url.format(query=query)
        
        logger.info(f"开始抓取搜索结果: {query}，最大页数: {max_pages}")
        
        for page in range(1, max_pages + 1):
            logger.info(f"抓取第 {page} 页: {current_url}")
            
            result = await self.scrape(
                url=current_url,
                selectors=[item_selector],
                scroll_to_load=True,
                item_selector=item_selector,
                **kwargs,
            )
            
            results.append(result)
            
            if not result.success:
                logger.warning(f"第 {page} 页抓取失败，停止分页")
                break
            
            # 检查是否还有下一页
            if not await self._has_next_page():
                logger.info("已到达最后一页")
                break
            
            # 导航到下一页
            if page < max_pages:
                next_url = await self._get_next_page_url()
                if next_url:
                    current_url = next_url
                else:
                    logger.warning("无法获取下一页 URL，停止分页")
                    break
        
        logger.info(f"搜索结果抓取完成: {len(results)} 页，共 {sum(len(r.items) for r in results)} 项")
        return results
    
    # =========================================================================
    # 导航与等待
    # =========================================================================
    
    async def _navigate(
        self,
        url: str,
        wait_for: Optional[str] = None,
        timeout: float = None,
    ) -> Dict[str, Any]:
        """导航到 URL 并等待页面就绪"""
        start_time = time.time()
        
        logger.info(f"导航到: {url}")
        
        try:
            # 使用 CDP 导航
            await self.session.send("Page.navigate", {"url": url})
            
            # 等待页面加载
            wait_strategy = wait_for or "adaptive"
            wait_result = await self._smart_wait.wait_for(
                wait_strategy,
                timeout=timeout or self.config.get("wait_timeout", 30.0),
            )
            
            elapsed = time.time() - start_time
            
            return {
                "success": wait_result.success,
                "elapsed": elapsed,
                "error": None if wait_result.success else "等待超时",
            }
            
        except Exception as e:
            elapsed = time.time() - start_time
            logger.error(f"导航失败: {e}")
            return {
                "success": False,
                "elapsed": elapsed,
                "error": str(e),
            }
    
    # =========================================================================
    # SPA 框架检测
    # =========================================================================
    
    async def _detect_framework(self) -> Optional[SPAFramework]:
        """检测 SPA 框架"""
        try:
            info = await self._spa_detector.detect()
            logger.info(f"检测到 SPA 框架: {info.framework.value} v{info.version}")
            return info.framework
        except Exception as e:
            logger.warning(f"SPA 框架检测失败: {e}")
            return None
    
    # =========================================================================
    # 滚动加载
    # =========================================================================
    
    async def _scroll_to_load(
        self,
        item_selector: str = "",
        max_pages: int = 10,
    ) -> Optional[ScrollResult]:
        """滚动加载更多内容"""
        if not item_selector:
            logger.debug("未指定 item_selector，跳过滚动加载")
            return None
        
        logger.info(f"开始滚动加载，最大页数: {max_pages}")
        start_time = time.time()
        
        try:
            result = await self._dynamic_loader.smart_scroll(
                max_pages=max_pages,
                item_selector=item_selector,
            )
            
            elapsed = time.time() - start_time
            result.total_time = elapsed
            
            logger.info(f"滚动加载完成: {result.pages_loaded} 页，{result.items_found} 项，耗时 {elapsed:.2f}s")
            return result
            
        except Exception as e:
            logger.error(f"滚动加载失败: {e}")
            return None
    
    # =========================================================================
    # 内容提取
    # =========================================================================
    
    async def _extract_content(
        self,
        selectors: Optional[List[str]] = None,
        extract_js: Optional[str] = None,
    ) -> List[ScrapedItem]:
        """提取页面内容"""
        start_time = time.time()
        items = []
        
        if extract_js:
            # 使用自定义 JS 提取
            logger.info("使用自定义 JS 提取内容")
            try:
                raw_data = await self.session.eval_js(extract_js)
                if isinstance(raw_data, list):
                    for i, item in enumerate(raw_data):
                        if isinstance(item, dict):
                            items.append(ScrapedItem(
                                index=i,
                                selector="custom",
                                text=item.get("text", ""),
                                href=item.get("href"),
                                attributes=item.get("attributes", {}),
                            ))
                elif isinstance(raw_data, dict):
                    items.append(ScrapedItem(
                        index=0,
                        selector="custom",
                        text=json.dumps(raw_data, ensure_ascii=False),
                    ))
            except Exception as e:
                logger.error(f"自定义 JS 提取失败: {e}")
        elif selectors:
            # 使用 CSS 选择器提取
            logger.info(f"使用选择器提取内容: {selectors}")
            for selector in selectors:
                try:
                    elements = await self.session.query_selector_all(selector)
                    for i, el in enumerate(elements):
                        rect = await el.bounding_box() if hasattr(el, 'bounding_box') else None
                        items.append(ScrapedItem(
                            index=len(items),
                            selector=selector,
                            text=await el.inner_text() if hasattr(el, 'inner_text') else "",
                            href=await el.get_attribute('href') if hasattr(el, 'get_attribute') else None,
                            rect=rect,
                        ))
                except Exception as e:
                    logger.warning(f"选择器 {selector} 提取失败: {e}")
        else:
            # 提取页面文本
            logger.info("提取页面文本")
            try:
                text = await self.session.eval_js("() => document.body.innerText")
                items.append(ScrapedItem(
                    index=0,
                    selector="body",
                    text=text[:5000] if text else "",  # 限制长度
                ))
            except Exception as e:
                logger.error(f"文本提取失败: {e}")
        
        elapsed = time.time() - start_time
        logger.info(f"内容提取完成: {len(items)} 项，耗时 {elapsed:.2f}s")
        
        return items
    
    # =========================================================================
    # 分页处理
    # =========================================================================
    
    async def _has_next_page(self) -> bool:
        """检查是否有下一页"""
        try:
            next_btn = await self.session.query_selector("a[href*='page'], .next-page, [rel='next']")
            return next_btn is not None
        except Exception:
            return False
    
    async def _get_next_page_url(self) -> Optional[str]:
        """获取下一页 URL"""
        try:
            url = await self.session.eval_js("""
                () => {
                    const selectors = ['a[href*="page"]', '.next-page', '[rel="next"]'];
                    for (const sel of selectors) {
                        const el = document.querySelector(sel);
                        if (el && el.href) return el.href;
                    }
                    return null;
                }
            """)
            return url
        except Exception:
            return None
    
    # =========================================================================
    # 统计更新
    # =========================================================================
    
    def _update_stats(self, result: ScrapeResult) -> None:
        """更新统计信息"""
        if result.success:
            self._stats["success_count"] += 1
            
            # 更新平均值
            total = self._stats["success_count"]
            self._stats["avg_wait_time"] = (
                (self._stats["avg_wait_time"] * (total - 1) + result.wait_time) / total
            )
            self._stats["avg_scroll_time"] = (
                (self._stats["avg_scroll_time"] * (total - 1) + result.scroll_time) / total
            )
            self._stats["avg_extract_time"] = (
                (self._stats["avg_extract_time"] * (total - 1) + result.extract_time) / total
            )
    
    def get_stats(self) -> Dict[str, Any]:
        """获取统计信息"""
        return self._stats.copy()
    
    def reset_stats(self) -> None:
        """重置统计信息"""
        self._stats = {
            "total_scrapes": 0,
            "success_count": 0,
            "failure_count": 0,
            "avg_wait_time": 0.0,
            "avg_scroll_time": 0.0,
            "avg_extract_time": 0.0,
        }


# ============================================================================
# 便捷函数
# ============================================================================

async def scrape_spa(
    session,
    url: str,
    selectors: Optional[List[str]] = None,
    scroll_to_load: bool = False,
    save_path: Optional[str] = None,
    **kwargs,
) -> ScrapeResult:
    """
    便捷函数：抓取 SPA 页面
    
    Args:
        session: CDP session 对象
        url: 目标 URL
        selectors: CSS 选择器列表
        scroll_to_load: 是否滚动加载
        save_path: 结果保存路径
        **kwargs: 其他参数
    
    Returns:
        ScrapeResult: 抓取结果
    """
    scraper = SPAScraper(session, kwargs.get("config", {}))
    return await scraper.scrape(
        url=url,
        selectors=selectors,
        scroll_to_load=scroll_to_load,
        save_path=save_path,
        **kwargs,
    )


async def scrape_search_results(
    session,
    search_url: str,
    query: str,
    item_selector: str,
    max_pages: int = 5,
    **kwargs,
) -> List[ScrapeResult]:
    """
    便捷函数：抓取搜索结果
    
    Args:
        session: CDP session 对象
        search_url: 搜索 URL 模板
        query: 搜索关键词
        item_selector: 列表项选择器
        max_pages: 最大页数
        **kwargs: 其他参数
    
    Returns:
        List[ScrapeResult]: 每页的抓取结果
    """
    scraper = SPAScraper(session, kwargs.get("config", {}))
    return await scraper.scrape_search(
        search_url=search_url,
        query=query,
        item_selector=item_selector,
        max_pages=max_pages,
        **kwargs,
    )
