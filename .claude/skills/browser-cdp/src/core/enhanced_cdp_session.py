"""
enhanced_cdp_session.py - 增强版 CDP 会话

集成所有增强模块，提供统一的增强 API。
"""
from __future__ import annotations

import logging
from typing import Optional, List, Dict, Any, Callable

from src.core.cdp_client import CDPSession
from src.core.smart_wait import SmartWait, WaitConfig
from src.core.retry_handler import RetryHandler, RetryConfig, FailureReason
from src.core.dynamic_loader import DynamicLoader, ScrollConfig, LazyLoadConfig
from src.core.complex_dom import ComplexDOMHandler, DOMScanConfig
from src.core.stealth import StealthMode, StealthConfig

logger = logging.getLogger(__name__)


class EnhancedCDPSession(CDPSession):
    """
    增强版 CDP 会话
    
    集成：
    - SmartWait: 智能等待策略
    - RetryHandler: 自动重试与熔断
    - DynamicLoader: 动态内容加载
    - ComplexDOMHandler: 复杂 DOM 处理
    - StealthMode: 反检测模式
    """
    
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        
        # 初始化各模块
        self.smart_wait = SmartWait(self)
        self.retry_handler = RetryHandler()
        self.dynamic_loader = DynamicLoader(self)
        self.complex_dom = ComplexDOMHandler(self)
        self.stealth = StealthMode(self)
        
        # 默认配置
        self.default_timeout = 30.0
        self.default_retry_count = 3
    
    # =========================================================================
    # 智能导航
    # =========================================================================
    
    async def goto(
        self,
        url: str,
        wait_for: str = "networkidle",
        timeout: float = None,
        stealth: bool = False
    ) -> bool:
        """
        智能导航到指定 URL
        
        Args:
            url: 目标 URL
            wait_for: 等待策略 (load/networkidle/route/stable/ajax/selector)
            timeout: 超时时间
            stealth: 是否启用 stealth 模式
        
        Returns:
            bool: 导航是否成功
        """
        timeout = timeout or self.default_timeout
        
        # 应用 stealth 模式
        if stealth:
            self.stealth.apply()
        
        # 执行导航
        async def _navigate():
            self.send("Page.navigate", {"url": url})
            return True
        
        # 带重试的导航
        await self.retry_handler.execute(_navigate)
        
        # 等待页面稳定
        if wait_for != "none":
            await self.smart_wait.wait_for(wait_for, timeout=timeout)
        
        return True
    
    # =========================================================================
    # 智能等待
    # =========================================================================
    
    async def wait_for_network_idle(self, idle_timeout: float = 0.5) -> bool:
        """等待网络空闲"""
        return await self.smart_wait.wait_for("networkidle", idle_timeout=idle_timeout)
    
    async def wait_for_selector(self, selector: str, timeout: float = None) -> bool:
        """等待选择器出现"""
        timeout = timeout or self.default_timeout
        return await self.smart_wait.wait_for("selector", selector=selector, timeout=timeout)
    
    async def wait_for_route(self, expected_url: str = None, timeout: float = None) -> bool:
        """等待 SPA 路由稳定"""
        timeout = timeout or self.default_timeout
        return await self.smart_wait.wait_for("route", expected_url=expected_url, timeout=timeout)
    
    async def wait_for_stable(self, check_interval: float = 0.5, stable_count: int = 3) -> bool:
        """等待内容稳定"""
        return await self.smart_wait.wait_for("stable", check_interval=check_interval, stable_count=stable_count)
    
    async def wait_for_ajax(self, timeout: float = None) -> bool:
        """等待 AJAX 请求完成"""
        timeout = timeout or self.default_timeout
        return await self.smart_wait.wait_for("ajax", timeout=timeout)
    
    # =========================================================================
    # 动态内容加载
    # =========================================================================
    
    async def scroll_to_load(
        self,
        max_pages: int = 10,
        scroll_delay: float = 0.5,
        callback: Callable[[int, int], None] = None
    ) -> int:
        """无限滚动加载"""
        return await self.dynamic_loader.scroll_to_load(
            max_pages=max_pages,
            scroll_delay=scroll_delay,
            callback=callback
        )
    
    async def scroll_until_not_found(
        self,
        selector: str,
        max_pages: int = 20,
        scroll_delay: float = 0.5
    ) -> int:
        """滚动直到元素不再出现"""
        return await self.dynamic_loader.scroll_until_not_found(
            selector=selector,
            max_pages=max_pages,
            scroll_delay=scroll_delay
        )
    
    async def wait_for_lazy_images(
        self,
        selector: str = None,
        timeout: float = None
    ) -> bool:
        """等待懒加载图片"""
        return await self.dynamic_loader.wait_for_lazy_images(selector, timeout)
    
    async def load_lazy_images(self, selector: str = None) -> int:
        """主动加载懒加载图片"""
        return await self.dynamic_loader.load_lazy_images(selector)
    
    async def collect_virtual_list(
        self,
        container_selector: str,
        item_selector: str,
        max_items: int = 100,
        scroll_delay: float = 0.3
    ) -> List[str]:
        """收集虚拟列表内容"""
        return await self.dynamic_loader.collect_virtual_list(
            container_selector,
            item_selector,
            max_items,
            scroll_delay
        )
    
    # =========================================================================
    # 复杂 DOM 处理
    # =========================================================================
    
    async def scan_shadow_dom(
        self,
        root_selector: str = "*",
        selector: str = None,
        max_depth: int = None
    ) -> List[Dict[str, Any]]:
        """扫描 Shadow DOM"""
        return await self.complex_dom.scan_shadow_dom(root_selector, selector, max_depth)
    
    async def access_iframe(
        self,
        iframe_selector: str,
        timeout: float = None
    ) -> Optional[Dict[str, Any]]:
        """访问 iframe 内容"""
        return await self.complex_dom.access_iframe(iframe_selector, timeout)
    
    async def scan_all_iframes(self) -> List[Dict[str, Any]]:
        """扫描所有 iframe"""
        return await self.complex_dom.scan_all_iframes()
    
    async def wait_for_custom_element(
        self,
        tag_name: str,
        timeout: float = None
    ) -> bool:
        """等待自定义元素定义"""
        return await self.complex_dom.wait_for_custom_element(tag_name, timeout)
    
    async def detect_virtual_list(
        self,
        container_selector: str = None
    ) -> Optional[Dict[str, Any]]:
        """检测虚拟列表"""
        return await self.complex_dom.detect_virtual_list(container_selector)
    
    async def scan_interactive_elements(
        self,
        include_shadow: bool = True,
        include_iframes: bool = True
    ) -> List[Dict[str, Any]]:
        """扫描所有可交互元素（增强版）"""
        return await self.complex_dom.scan_interactive_elements(
            include_shadow,
            include_iframes
        )
    
    # =========================================================================
    # 反检测模式
    # =========================================================================
    
    async def enable_stealth(self) -> bool:
        """启用 stealth 模式"""
        return self.stealth.apply()

    async def human_like_click(self, x: float, y: float, duration: float = 0.3):
        """模拟人类点击"""
        self.stealth.human_like_click(x, y, duration)

    async def human_like_type(self, text: str, min_delay: float = 0.05, max_delay: float = 0.15):
        """模拟人类打字"""
        self.stealth.human_like_type(text, min_delay, max_delay)

    async def human_like_scroll(self, delta_y: float, duration: float = 0.5):
        """模拟人类滚动"""
        self.stealth.human_like_scroll(delta_y, duration)

    async def random_delay(self, min_seconds: float = 0.1, max_seconds: float = 0.5):
        """随机延迟"""
        self.stealth.random_delay(min_seconds, max_seconds)

    async def random_human_delay(self):
        """随机人类化延迟"""
        self.stealth.random_human_delay()

    def get_random_user_agent(self) -> str:
        """获取随机用户代理"""
        return self.stealth.get_random_user_agent()

    async def set_user_agent(self, user_agent: str = None):
        """设置用户代理"""
        self.stealth.set_user_agent(user_agent)
    
    # =========================================================================
    # 便捷方法
    # =========================================================================
    
    async def fetch_page(
        self,
        url: str,
        wait_for: str = "networkidle",
        stealth: bool = False,
        timeout: float = None
    ) -> Dict[str, Any]:
        """
        一站式页面抓取
        
        Args:
            url: 目标 URL
            wait_for: 等待策略
            stealth: 是否启用 stealth
            timeout: 超时时间
        
        Returns:
            Dict: 页面信息（URL、标题、文本、链接等）
        """
        # 导航
        await self.goto(url, wait_for=wait_for, stealth=stealth, timeout=timeout)
        
        # 等待懒加载
        await self.wait_for_lazy_images()
        
        # 获取页面信息
        info = await self.get_page_info()
        
        return info
    
    async def get_page_info(self) -> Dict[str, Any]:
        """获取页面基本信息"""
        js = """
        (() => {
            return {
                url: location.href,
                title: document.title,
                text: document.body ? document.body.innerText.slice(0, 10000) : '',
                links: Array.from(document.querySelectorAll('a[href]')).map(a => ({
                    text: (a.innerText || '').trim().slice(0, 100),
                    href: a.href
                })).slice(0, 100),
                images: Array.from(document.querySelectorAll('img')).map(img => ({
                    src: img.src,
                    alt: img.alt || '',
                    width: img.naturalWidth,
                    height: img.naturalHeight
                })).slice(0, 50)
            };
        })()
        """
        return await self.eval_js(js)
    
    async def scrape_list(
        self,
        item_selector: str,
        fields: Dict[str, str],
        max_items: int = 100,
        scroll_to_load: bool = True
    ) -> List[Dict[str, Any]]:
        """
        抓取列表数据
        
        Args:
            item_selector: 列表项选择器
            fields: 字段映射 {显示名: CSS 选择器}
            max_items: 最大抓取数量
            scroll_to_load: 是否自动滚动加载
        
        Returns:
            List[Dict]: 抓取的数据列表
        """
        items = []
        
        # 滚动加载
        if scroll_to_load:
            await self.scroll_to_load(max_pages=10)
        
        # 获取所有匹配项
        js = f"""
        (() => {{
            const elements = document.querySelectorAll('{item_selector}');
            const results = [];
            
            for (const el of elements) {{
                if (results.length >= {max_items}) break;
                
                const item = {{}};
                {', '.join([f"item[{key!r}] = el.querySelector({value!r}) ? el.querySelector({value!r}).innerText.trim() : '';" for key, value in fields.items()])}
                
                results.push(item);
            }}
            
            return results;
        }})()
        """
        
        items = await self.eval_js(js)
        logger.info(f"抓取完成，共 {len(items) if items else 0} 条数据")
        
        return items or []
