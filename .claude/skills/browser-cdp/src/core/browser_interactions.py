"""
browser_interactions.py - 浏览器交互操作统一入口

整合所有浏览器交互能力，提供统一的调用接口：
1. 动态页面支持（等待、滚动、SPA路由）
2. 表单操作（填写、提交、验证）
3. 弹窗处理（自动关闭、确认、取消）
4. AJAX 请求监控
5. 页面状态管理
6. 错误恢复策略

用法：
    from src.core.browser_interactions import BrowserInteractions
    
    interactions = BrowserInteractions(session)
    
    # 等待页面就绪
    await interactions.wait_for_page_ready(timeout=30)
    
    # 滚动加载内容
    items = await interactions.scroll_and_collect(".item", max_items=100)
    
    # 提交表单
    await interactions.submit_form("#search-form", {"keyword": "AI"})
    
    # 处理弹窗
    await interactions.handle_popup(timeout=10)
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable, Tuple

from src.core.dynamic_page_support import DynamicPageSupport, DynamicPageResult
from src.core.browser_interaction import (
    BrowserInteraction,
    InteractionResult,
    PopupType,
    ErrorRecoveryStrategy,
)
from src.core.smart_wait import SmartWait

logger = logging.getLogger(__name__)


@dataclass
class InteractionStats:
    """交互操作统计"""
    total_operations: int = 0
    successful_operations: int = 0
    failed_operations: int = 0
    total_elapsed: float = 0.0
    
    def record_success(self, elapsed: float):
        self.total_operations += 1
        self.successful_operations += 1
        self.total_elapsed += elapsed
    
    def record_failure(self, elapsed: float):
        self.total_operations += 1
        self.failed_operations += 1
        self.total_elapsed += elapsed
    
    def to_dict(self) -> dict:
        return {
            "total_operations": self.total_operations,
            "successful_operations": self.successful_operations,
            "failed_operations": self.failed_operations,
            "success_rate": (
                self.successful_operations / self.total_operations * 100
                if self.total_operations > 0 else 0.0
            ),
            "avg_elapsed": (
                self.total_elapsed / self.successful_operations
                if self.successful_operations > 0 else 0.0
            ),
        }


class BrowserInteractions:
    """
    浏览器交互操作统一入口类
    
    整合动态页面支持、表单操作、弹窗处理、AJAX监控等能力
    """
    
    def __init__(self, session, config: Optional[Dict[str, Any]] = None):
        self.session = session
        self.config = config or {}
        self._dynamic_support = DynamicPageSupport(session)
        self._interaction = BrowserInteraction(session)
        self._smart_wait = SmartWait(session)
        self._stats = InteractionStats()
        self._error_handlers: Dict[str, Callable] = {}
        self._register_default_error_handlers()
    
    def _register_default_error_handlers(self):
        """注册默认错误处理器"""
        self._error_handlers["TimeoutError"] = self._handle_timeout
        self._error_handlers["ElementNotFoundError"] = self._handle_element_not_found
        self._error_handlers["ConnectionError"] = self._handle_connection_lost
    
    # =========================================================================
    # 动态页面支持
    # =========================================================================
    
    async def wait_for_page_ready(
        self,
        selector: str = None,
        timeout: float = 30.0,
        wait_network_idle: bool = True,
        wait_content_stable: bool = True,
    ) -> bool:
        """
        等待页面完全就绪
        
        Args:
            selector: 关键元素选择器
            timeout: 超时时间
            wait_network_idle: 是否等待网络空闲
            wait_content_stable: 是否等待内容稳定
        
        Returns:
            bool: 是否就绪
        """
        start = time.time()
        try:
            result = await self._dynamic_support.wait_for_page_ready(
                selector=selector,
                wait_network_idle=wait_network_idle,
                wait_content_stable=wait_content_stable,
                timeout=timeout,
            )
            self._stats.record_success(time.time() - start)
            logger.info(f"页面就绪检查完成: {result}, 耗时 {time.time()-start:.2f}s")
            return result
        except Exception as e:
            self._stats.record_failure(time.time() - start)
            logger.error(f"页面就绪检查失败: {e}")
            return False
    
    async def wait_for_element(
        self,
        selector: str,
        timeout: float = 10.0,
        visible: bool = True,
    ) -> bool:
        """
        等待元素出现
        
        Args:
            selector: CSS 选择器
            timeout: 超时时间
            visible: 是否等待可见
        
        Returns:
            bool: 是否成功
        """
        start = time.time()
        try:
            result = await self._dynamic_support.wait_for_element(
                selector=selector,
                timeout=timeout,
                visible=visible,
            )
            self._stats.record_success(time.time() - start)
            return result
        except Exception as e:
            self._stats.record_failure(time.time() - start)
            logger.error(f"等待元素失败: {e}")
            return False
    
    async def scroll_to_load(
        self,
        item_selector: str = "",
        max_pages: int = 10,
        max_items: int = 100,
    ) -> Dict[str, Any]:
        """
        滚动加载内容
        
        Args:
            item_selector: 列表项选择器
            max_pages: 最大滚动页数
            max_items: 最大收集项数
        
        Returns:
            Dict: 滚动结果
        """
        start = time.time()
        try:
            result = await self._dynamic_support.scroll_to_load(
                item_selector=item_selector,
                max_pages=max_pages,
                max_items=max_items,
            )
            self._stats.record_success(time.time() - start)
            return {
                "pages_loaded": result.pages_loaded,
                "items_found": result.items_found,
                "success": result.success,
            }
        except Exception as e:
            self._stats.record_failure(time.time() - start)
            logger.error(f"滚动加载失败: {e}")
            return {"pages_loaded": 0, "items_found": 0, "success": False}
    
    async def scroll_and_collect(
        self,
        item_selector: str,
        max_items: int = 100,
        max_pages: int = 10,
    ) -> List[Dict[str, Any]]:
        """
        滚动并收集内容
        
        Args:
            item_selector: 列表项选择器
            max_items: 最大收集项数
            max_pages: 最大滚动页数
        
        Returns:
            List[Dict]: 收集的数据列表
        """
        start = time.time()
        try:
            items = await self._dynamic_support.scroll_and_collect(
                item_selector=item_selector,
                max_items=max_items,
                max_pages=max_pages,
            )
            self._stats.record_success(time.time() - start)
            logger.info(f"滚动收集完成: {len(items)} 项")
            return items
        except Exception as e:
            self._stats.record_failure(time.time() - start)
            logger.error(f"滚动收集失败: {e}")
            return []
    
    async def wait_for_lazy_images(
        self,
        selector: str = "img[loading='lazy'], [data-src], [data-lazy]",
        timeout: float = 10.0,
    ) -> int:
        """
        等待懒加载图片完成
        
        Args:
            selector: 懒加载图片选择器
            timeout: 超时时间
        
        Returns:
            int: 已加载的图片数量
        """
        start = time.time()
        try:
            loaded = await self._dynamic_support.wait_for_lazy_images(
                selector=selector,
                timeout=timeout,
            )
            self._stats.record_success(time.time() - start)
            logger.info(f"懒加载图片等待完成: {loaded} 张")
            return loaded
        except Exception as e:
            self._stats.record_failure(time.time() - start)
            logger.error(f"懒加载图片等待失败: {e}")
            return 0
    
    async def wait_for_dom_stable(
        self,
        check_interval: float = 0.5,
        stable_count: int = 3,
        timeout: float = 30.0,
    ) -> bool:
        """
        等待 DOM 稳定
        
        Args:
            check_interval: 检查间隔
            stable_count: 连续稳定次数
            timeout: 超时时间
        
        Returns:
            bool: 是否稳定
        """
        start = time.time()
        try:
            result = await self._dynamic_support.wait_for_dom_stable(
                check_interval=check_interval,
                stable_count=stable_count,
                timeout=timeout,
            )
            self._stats.record_success(time.time() - start)
            return result
        except Exception as e:
            self._stats.record_failure(time.time() - start)
            logger.error(f"DOM 稳定等待失败: {e}")
            return False
    
    async def detect_spa(self) -> Dict[str, Any]:
        """
        检测 SPA 框架
        
        Returns:
            Dict: SPA 框架信息
        """
        start = time.time()
        try:
            info = await self._dynamic_support.detect_spa()
            self._stats.record_success(time.time() - start)
            return {
                "framework": info.framework.value,
                "version": info.version,
                "is_spa": info.is_spa,
            }
        except Exception as e:
            self._stats.record_failure(time.time() - start)
            logger.error(f"SPA 检测失败: {e}")
            return {"framework": "unknown", "version": "0", "is_spa": False}
    
    # =========================================================================
    # 表单操作
    # =========================================================================
    
    async def fill_form(
        self,
        form_selector: str,
        fields: Dict[str, Any],
    ) -> InteractionResult:
        """
        填写表单
        
        Args:
            form_selector: 表单选择器
            fields: 字段值字典
        
        Returns:
            InteractionResult: 操作结果
        """
        start = time.time()
        try:
            result = await self._interaction.submit_form(
                form_selector=form_selector,
                fields=fields,
                wait_for_response=False,
            )
            self._stats.record_success(time.time() - start)
            return result
        except Exception as e:
            self._stats.record_failure(time.time() - start)
            logger.error(f"表单填写失败: {e}")
            return InteractionResult(
                success=False,
                operation="fill_form",
                error=str(e),
                elapsed=time.time() - start,
            )
    
    async def submit_form(
        self,
        form_selector: str,
        fields: Dict[str, Any],
        wait_for_response: bool = True,
        timeout: float = 30.0,
    ) -> InteractionResult:
        """
        提交表单
        
        Args:
            form_selector: 表单选择器
            fields: 字段值字典
            wait_for_response: 是否等待响应
            timeout: 超时时间
        
        Returns:
            InteractionResult: 操作结果
        """
        start = time.time()
        try:
            result = await self._interaction.submit_form(
                form_selector=form_selector,
                fields=fields,
                wait_for_response=wait_for_response,
                timeout=timeout,
            )
            self._stats.record_success(time.time() - start)
            return result
        except Exception as e:
            self._stats.record_failure(time.time() - start)
            logger.error(f"表单提交失败: {e}")
            return InteractionResult(
                success=False,
                operation="submit_form",
                error=str(e),
                elapsed=time.time() - start,
            )
    
    # =========================================================================
    # 弹窗处理
    # =========================================================================
    
    async def handle_popup(
        self,
        popup_type: PopupType = None,
        action: str = "close",
        timeout: float = 10.0,
    ) -> InteractionResult:
        """
        处理弹窗
        
        Args:
            popup_type: 弹窗类型
            action: 操作类型（close/accept/cancel）
            timeout: 超时时间
        
        Returns:
            InteractionResult: 操作结果
        """
        start = time.time()
        try:
            result = await self._interaction.handle_popup(
                popup_type=popup_type,
                action=action,
                timeout=timeout,
            )
            self._stats.record_success(time.time() - start)
            return result
        except Exception as e:
            self._stats.record_failure(time.time() - start)
            logger.error(f"弹窗处理失败: {e}")
            return InteractionResult(
                success=False,
                operation="handle_popup",
                error=str(e),
                elapsed=time.time() - start,
            )
    
    async def auto_handle_popups(
        self,
        timeout: float = 10.0,
        max_attempts: int = 3,
    ) -> List[InteractionResult]:
        """
        自动处理所有弹窗
        
        Args:
            timeout: 每次检测超时
            max_attempts: 最大尝试次数
        
        Returns:
            List[InteractionResult]: 处理结果列表
        """
        results = []
        for attempt in range(max_attempts):
            result = await self.handle_popup(timeout=timeout)
            if not result.data.get("popup_detected", False):
                break
            results.append(result)
        return results
    
    # =========================================================================
    # AJAX 监控
    # =========================================================================
    
    async def wait_for_ajax(
        self,
        timeout: float = 15.0,
    ) -> List[Dict[str, Any]]:
        """
        等待 AJAX 请求完成
        
        Args:
            timeout: 超时时间
        
        Returns:
            List[Dict]: AJAX 请求列表
        """
        start = time.time()
        try:
            requests = await self._interaction.wait_for_ajax(timeout=timeout)
            self._stats.record_success(time.time() - start)
            return [
                {
                    "url": req.url,
                    "method": req.method,
                    "status": req.status,
                    "duration": req.duration,
                }
                for req in requests
            ]
        except Exception as e:
            self._stats.record_failure(time.time() - start)
            logger.error(f"AJAX 等待失败: {e}")
            return []
    
    async def monitor_ajax_requests(
        self,
        url_pattern: str = None,
        timeout: float = 30.0,
    ) -> List[Dict[str, Any]]:
        """
        监控 AJAX 请求
        
        Args:
            url_pattern: URL 模式过滤
            timeout: 超时时间
        
        Returns:
            List[Dict]: 监控到的请求列表
        """
        start = time.time()
        try:
            requests = await self._interaction.monitor_ajax_requests(
                url_pattern=url_pattern,
                timeout=timeout,
            )
            self._stats.record_success(time.time() - start)
            return [
                {
                    "url": req.url,
                    "method": req.method,
                    "status": req.status,
                    "duration": req.duration,
                }
                for req in requests
            ]
        except Exception as e:
            self._stats.record_failure(time.time() - start)
            logger.error(f"AJAX 监控失败: {e}")
            return []
    
    # =========================================================================
    # 页面状态管理
    # =========================================================================
    
    async def capture_page_state(self) -> Dict[str, Any]:
        """
        捕获页面状态快照
        
        Returns:
            Dict: 页面状态信息
        """
        start = time.time()
        try:
            state = await self._interaction.capture_page_state()
            self._stats.record_success(time.time() - start)
            return {
                "url": state.url,
                "title": state.title,
                "scroll_position": state.scroll_position,
                "page_height": state.page_height,
                "element_count": state.element_count,
                "timestamp": state.timestamp,
            }
        except Exception as e:
            self._stats.record_failure(time.time() - start)
            logger.error(f"页面状态捕获失败: {e}")
            return {}
    
    def get_page_state_history(self, limit: int = 10) -> List[Dict[str, Any]]:
        """
        获取页面状态历史
        
        Args:
            limit: 返回数量限制
        
        Returns:
            List[Dict]: 状态历史列表
        """
        states = self._interaction.get_page_state_history(limit=limit)
        return [
            {
                "url": s.url,
                "title": s.title,
                "scroll_position": s.scroll_position,
                "page_height": s.page_height,
                "element_count": s.element_count,
                "timestamp": s.timestamp,
            }
            for s in states
        ]
    
    # =========================================================================
    # 组合操作
    # =========================================================================
    
    async def search_and_collect(
        self,
        search_url: str,
        query: str,
        item_selector: str,
        max_items: int = 50,
    ) -> InteractionResult:
        """
        搜索并收集结果（组合操作）
        
        Args:
            search_url: 搜索页面 URL
            query: 搜索关键词
            item_selector: 结果项选择器
            max_items: 最大收集项数
        
        Returns:
            InteractionResult: 操作结果
        """
        start = time.time()
        try:
            result = await self._interaction.search_and_collect(
                search_url=search_url,
                query=query,
                item_selector=item_selector,
                max_items=max_items,
            )
            self._stats.record_success(time.time() - start)
            return result
        except Exception as e:
            self._stats.record_failure(time.time() - start)
            logger.error(f"搜索收集失败: {e}")
            return InteractionResult(
                success=False,
                operation="search_and_collect",
                error=str(e),
                elapsed=time.time() - start,
            )
    
    async def navigate_and_collect(
        self,
        url: str,
        item_selector: str,
        max_items: int = 100,
        wait_for_ready: bool = True,
    ) -> List[Dict[str, Any]]:
        """
        导航并收集内容（组合操作）
        
        Args:
            url: 目标 URL
            item_selector: 列表项选择器
            max_items: 最大收集项数
            wait_for_ready: 是否等待页面就绪
        
        Returns:
            List[Dict]: 收集的数据列表
        """
        start = time.time()
        try:
            if wait_for_ready:
                await self.wait_for_page_ready(timeout=30.0)
            
            items = await self.scroll_and_collect(
                item_selector=item_selector,
                max_items=max_items,
            )
            
            self._stats.record_success(time.time() - start)
            logger.info(f"导航收集完成: {len(items)} 项，耗时 {time.time()-start:.2f}s")
            return items
        except Exception as e:
            self._stats.record_failure(time.time() - start)
            logger.error(f"导航收集失败: {e}")
            return []
    
    # =========================================================================
    # 错误恢复
    # =========================================================================
    
    async def recover_from_error(
        self,
        error: Exception,
        strategy: ErrorRecoveryStrategy = ErrorRecoveryStrategy.RETRY,
        max_retries: int = 3,
    ) -> Tuple[bool, str]:
        """
        执行错误恢复
        
        Args:
            error: 发生的错误
            strategy: 恢复策略
            max_retries: 最大重试次数
        
        Returns:
            Tuple[bool, str]: (是否成功, 恢复消息)
        """
        handler = self._error_handlers.get(type(error).__name__, None)
        if handler:
            return await handler(error, strategy, max_retries)
        return await self._interaction._error_recovery.recover(
            error, strategy, max_retries
        )
    
    async def _handle_timeout(self, error: Exception, strategy: ErrorRecoveryStrategy, max_retries: int) -> Tuple[bool, str]:
        """处理超时错误"""
        logger.warning(f"超时错误恢复: {error}")
        if strategy == ErrorRecoveryStrategy.RETRY:
            # 直接调用 recover 而不是使用 asyncio.run()
            return await self._interaction._error_recovery.recover(
                error, ErrorRecoveryStrategy.RETRY, max_retries
            )
        return True, "Timeout error handled"
    
    async def _handle_element_not_found(self, error: Exception, strategy: ErrorRecoveryStrategy, max_retries: int) -> Tuple[bool, str]:
        """处理元素未找到错误"""
        logger.warning(f"元素未找到错误恢复: {error}")
        return True, "Element not found, skipped"
    
    async def _handle_connection_lost(self, error: Exception, strategy: ErrorRecoveryStrategy, max_retries: int) -> Tuple[bool, str]:
        """处理连接丢失错误"""
        logger.error(f"连接丢失错误: {error}")
        return False, "Connection lost, cannot recover"
    
    # =========================================================================
    # 统计信息
    # =========================================================================
    
    def get_stats(self) -> Dict[str, Any]:
        """
        获取交互操作统计
        
        Returns:
            Dict: 统计信息
        """
        return self._stats.to_dict()
    
    def reset_stats(self):
        """重置统计信息"""
        self._stats = InteractionStats()
        logger.info("交互统计已重置")


# ============================================================================
# 便捷函数
# ============================================================================

async def wait_for_page_ready(
    session,
    selector: str = None,
    timeout: float = 30.0,
) -> bool:
    """等待页面完全就绪的便捷函数"""
    interactions = BrowserInteractions(session)
    return await interactions.wait_for_page_ready(selector=selector, timeout=timeout)


async def scroll_and_collect(
    session,
    item_selector: str,
    max_items: int = 100,
    max_pages: int = 10,
) -> List[Dict[str, Any]]:
    """滚动并收集内容的便捷函数"""
    interactions = BrowserInteractions(session)
    return await interactions.scroll_and_collect(
        item_selector=item_selector,
        max_items=max_items,
        max_pages=max_pages,
    )


async def handle_popup(
    session,
    popup_type: PopupType = None,
    action: str = "close",
    timeout: float = 10.0,
) -> InteractionResult:
    """处理弹窗的便捷函数"""
    interactions = BrowserInteractions(session)
    return await interactions.handle_popup(
        popup_type=popup_type,
        action=action,
        timeout=timeout,
    )


async def wait_for_ajax(
    session,
    timeout: float = 15.0,
) -> List[Dict[str, Any]]:
    """等待 AJAX 请求完成的便捷函数"""
    interactions = BrowserInteractions(session)
    return await interactions.wait_for_ajax(timeout=timeout)


async def search_and_collect(
    session,
    search_url: str,
    query: str,
    item_selector: str,
    max_items: int = 50,
) -> InteractionResult:
    """搜索并收集结果的便捷函数"""
    interactions = BrowserInteractions(session)
    return await interactions.search_and_collect(
        search_url=search_url,
        query=query,
        item_selector=item_selector,
        max_items=max_items,
    )
