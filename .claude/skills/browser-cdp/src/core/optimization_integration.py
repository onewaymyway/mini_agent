"""
optimization_integration.py - 优化集成模块

整合所有优化功能：
- 增强重试策略
- UA 轮换
- 反检测机制
- 智能选择器
- 自适应等待
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page, BrowserContext

from .enhanced_retry_strategy import (
    EnhancedRetryStrategy,
    RetryConfig,
    RetryResult,
    ErrorType,
)
from .ua_rotator import UARotator, UARotationConfig, get_ua_rotator
from .anti_detection import AntiDetectionManager, AntiDetectionConfig, get_anti_detection
from .smart_selector import SmartSelector, WebsiteSelectorManager, SelectorConfig
from .smart_wait import SmartWait, WaitConfig as SmartWaitConfig

logger = logging.getLogger(__name__)


@dataclass
class OptimizationConfig:
    """优化配置"""
    # 重试策略
    retry_config: RetryConfig = field(default_factory=RetryConfig)
    
    # UA 轮换
    ua_config: UARotationConfig = field(default_factory=UARotationConfig)
    
    # 反检测
    anti_detection_config: AntiDetectionConfig = field(default_factory=AntiDetectionConfig)
    
    # 智能等待
    wait_config: SmartWaitConfig = field(default_factory=SmartWaitConfig)
    
    # 选择器管理
    selector_config_dir: str = "config/websites"
    
    # 性能监控
    enable_performance_monitoring: bool = True
    performance_log_interval: int = 10  # 每 N 次操作记录一次性能


class OptimizationIntegration:
    """
    优化集成管理器
    
    整合所有优化功能，提供统一的接口
    """
    
    def __init__(self, config: Optional[OptimizationConfig] = None):
        self.config = config or OptimizationConfig()
        
        # 初始化各模块
        self.retry_strategy = EnhancedRetryStrategy(self.config.retry_config)
        self.ua_rotator = UARotator(self.config.ua_config)
        self.anti_detection = AntiDetectionManager(self.config.anti_detection_config)
        self.smart_wait = SmartWait(self.config.wait_config)
        self.selector_manager = WebsiteSelectorManager(self.config.selector_config_dir)
        
        # 性能统计
        self._performance_stats: Dict[str, List[float]] = {}
        self._operation_count = 0
    
    async def navigate_with_optimization(
        self,
        page: Page,
        url: str,
        site: Optional[str] = None,
        timeout: int = 30000,
    ) -> RetryResult:
        """
        带优化的导航
        
        Args:
            page: Playwright page 对象
            url: 目标 URL
            site: 网站标识
            timeout: 超时时间（毫秒）
            
        Returns:
            RetryResult: 导航结果
        """
        async def _navigate():
            # 请求前处理
            headers = await self.anti_detection.before_request(page, site)
            
            # 执行导航
            response = await page.goto(url, timeout=timeout, wait_until="networkidle")
            
            # 请求后处理
            await self.anti_detection.after_action(page, "navigate")
            
            return response
        
        return await self.retry_strategy.execute_with_retry(
            f"navigate:{url}",
            _navigate,
        )
    
    async def find_element_with_optimization(
        self,
        page: Page,
        site: str,
        selector_type: str,
        timeout: int = 10000,
    ) -> Optional[Dict]:
        """
        带优化的元素查找
        
        Args:
            page: Playwright page 对象
            site: 网站标识
            selector_type: 选择器类型
            timeout: 超时时间（毫秒）
            
        Returns:
            元素信息字典，失败返回 None
        """
        # 获取网站选择器管理器
        selector = self.selector_manager.get_manager(site)
        if not selector:
            logger.warning(f"未找到网站 {site} 的选择器配置")
            return None
        
        async def _find():
            # 智能等待
            wait_time = await self.smart_wait.get_smart_wait_time(site, selector_type)
            
            # 查找元素
            result = await selector.find(page, selector_type, timeout=wait_time)
            
            # 操作后处理
            await self.anti_detection.after_action(page, "find")
            
            return result
        
        result = await self.retry_strategy.execute_with_retry(
            f"find:{site}:{selector_type}",
            _find,
        )
        
        return result.result if result.success else None
    
    async def input_text_with_optimization(
        self,
        page: Page,
        site: str,
        selector_type: str,
        text: str,
    ) -> RetryResult:
        """
        带优化的文本输入
        
        Args:
            page: Playwright page 对象
            site: 网站标识
            selector_type: 选择器类型
            text: 要输入的文本
            
        Returns:
            RetryResult: 输入结果
        """
        async def _input():
            # 查找输入框
            selector = self.selector_manager.get_manager(site)
            if not selector:
                return RetryResult(success=False, error=Exception("未找到选择器配置"))
            
            element = await selector.find(page, selector_type, timeout=5000)
            if not element:
                return RetryResult(success=False, error=Exception(f"未找到元素: {selector_type}"))
            
            # 模拟人类打字
            await self.anti_detection.simulate_typing(
                page,
                element["selector"],
                text,
            )
            
            # 操作后处理
            await self.anti_detection.after_action(page, "input")
            
            return RetryResult(success=True)
        
        return await self.retry_strategy.execute_with_retry(
            f"input:{site}:{selector_type}",
            _input,
        )
    
    async def extract_data_with_optimization(
        self,
        page: Page,
        site: str,
        selector_type: str,
    ) -> Optional[List[Dict]]:
        """
        带优化的数据提取
        
        Args:
            page: Playwright page 对象
            site: 网站标识
            selector_type: 选择器类型
            
        Returns:
            提取的数据列表，失败返回 None
        """
        async def _extract():
            # 智能等待
            wait_time = await self.smart_wait.get_smart_wait_time(site, selector_type)
            
            # 查找元素
            selector = self.selector_manager.get_manager(site)
            if not selector:
                return None
            
            elements = await selector.find_all(page, selector_type, timeout=wait_time)
            
            # 操作后处理
            await self.anti_detection.after_action(page, "extract")
            
            return elements
        
        result = await self.retry_strategy.execute_with_retry(
            f"extract:{site}:{selector_type}",
            _extract,
        )
        
        return result.result if result.success else None
    
    async def scroll_with_optimization(
        self,
        page: Page,
        site: str,
        distance: int = 800,
    ) -> RetryResult:
        """
        带优化的滚动操作
        
        Args:
            page: Playwright page 对象
            site: 网站标识
            distance: 滚动距离（像素）
            
        Returns:
            RetryResult: 滚动结果
        """
        async def _scroll():
            # 智能等待页面加载完成
            await self.smart_wait.wait_for_page_ready(page, site)
            
            # 执行滚动
            await page.evaluate(f"window.scrollBy(0, {distance})")
            
            # 滚动后等待内容加载
            await asyncio.sleep(0.5)
            
            # 操作后处理
            await self.anti_detection.after_action(page, "scroll")
            
            return RetryResult(success=True)
        
        return await self.retry_strategy.execute_with_retry(
            f"scroll:{site}",
            _scroll,
        )
    
    def get_performance_stats(self) -> Dict[str, Any]:
        """获取性能统计"""
        stats = {
            "retry_stats": self.retry_strategy.get_stats(),
            "anti_detection_stats": self.anti_detection.get_stats(),
            "ua_stats": self.ua_rotator.get_stats(),
            "operation_count": self._operation_count,
        }
        
        if self._performance_stats:
            stats["avg_response_times"] = {
                k: sum(v) / len(v) if v else 0
                for k, v in self._performance_stats.items()
            }
        
        return stats
    
    def record_performance(self, operation: str, duration: float):
        """记录性能数据"""
        if not self.config.enable_performance_monitoring:
            return
        
        if operation not in self._performance_stats:
            self._performance_stats[operation] = []
        
        self._performance_stats[operation].append(duration)
        
        # 限制记录数量
        if len(self._performance_stats[operation]) > 100:
            self._performance_stats[operation] = self._performance_stats[operation][-50:]
        
        self._operation_count += 1
        
        # 定期记录日志
        if self._operation_count % self.config.performance_log_interval == 0:
            logger.debug(f"性能统计：已执行 {self._operation_count} 次操作")


# 全局单例
_optimization_integration: Optional[OptimizationIntegration] = None


def get_optimization_integration() -> OptimizationIntegration:
    """获取全局优化集成单例"""
    global _optimization_integration
    if _optimization_integration is None:
        _optimization_integration = OptimizationIntegration()
    return _optimization_integration


def set_optimization_integration(integration: OptimizationIntegration):
    """设置全局优化集成"""
    global _optimization_integration
    _optimization_integration = integration


def reset_optimization_integration():
    """重置全局优化集成"""
    global _optimization_integration
    _optimization_integration = None


# 便捷函数
async def optimized_navigate(page: Page, url: str, site: Optional[str] = None) -> RetryResult:
    """便捷函数：带优化的导航"""
    return await get_optimization_integration().navigate_with_optimization(page, url, site)


async def optimized_find(page: Page, site: str, selector_type: str) -> Optional[Dict]:
    """便捷函数：带优化的元素查找"""
    return await get_optimization_integration().find_element_with_optimization(page, site, selector_type)


async def optimized_input(page: Page, site: str, selector_type: str, text: str) -> RetryResult:
    """便捷函数：带优化的文本输入"""
    return await get_optimization_integration().input_text_with_optimization(page, site, selector_type, text)


async def optimized_extract(page: Page, site: str, selector_type: str) -> Optional[List[Dict]]:
    """便捷函数：带优化的数据提取"""
    return await get_optimization_integration().extract_data_with_optimization(page, site, selector_type)
