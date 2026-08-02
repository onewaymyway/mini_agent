"""
dynamic_loader.py - 动态内容加载模块

处理无限滚动、懒加载、虚拟列表等动态内容场景。

核心功能：
- 无限滚动自动加载
- 懒加载图片等待
- 虚拟列表元素收集
- 动态内容稳定性检测
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional, List, Dict, Any, Callable
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class ScrollConfig:
    """滚动配置"""
    max_pages: int = 10  # 最大滚动页数
    scroll_delay: float = 0.5  # 每次滚动后的等待时间
    scroll_amount: int = 800  # 每次滚动距离（像素）
    height_threshold: int = 50  # 高度变化阈值（像素）


@dataclass
class LazyLoadConfig:
    """懒加载配置"""
    selector: str = "img[loading='lazy'], [data-src], [data-lazy]"
    timeout: float = 10.0
    check_interval: float = 0.3


class DynamicLoader:
    """
    动态内容加载器
    
    处理各种动态加载场景：
    1. 无限滚动（无限列表）
    2. 懒加载图片/内容
    3. 虚拟列表（Virtual Scroll）
    4. AJAX 动态内容
    """
    
    def __init__(self, session):
        self.session = session
        self.scroll_config = ScrollConfig()
        self.lazy_config = LazyLoadConfig()
    
    # =========================================================================
    # 无限滚动
    # =========================================================================
    
    async def scroll_to_load(
        self,
        max_pages: int = None,
        scroll_delay: float = None,
        height_threshold: int = None,
        callback: Callable[[int, int], None] = None
    ) -> int:
        """
        无限滚动加载内容
        
        Args:
            max_pages: 最大滚动页数
            scroll_delay: 每次滚动后的等待时间
            height_threshold: 高度变化阈值
            callback: 每页加载后的回调 (pages_loaded, current_height)
        
        Returns:
            int: 实际加载的页数
        """
        max_pages = max_pages or self.scroll_config.max_pages
        scroll_delay = scroll_delay or self.scroll_config.scroll_delay
        height_threshold = height_threshold or self.scroll_config.height_threshold
        
        loaded_pages = 0
        previous_height = 0
        
        logger.info(f"开始无限滚动加载，最大页数: {max_pages}")
        
        for page in range(max_pages):
            # 获取当前滚动高度
            current_height = await self._get_scroll_height()
            
            # 如果高度没变化，说明已加载完所有内容
            if abs(current_height - previous_height) < height_threshold:
                logger.info(f"滚动高度无变化，停止加载 (page {page})")
                break
            
            previous_height = current_height
            loaded_pages += 1
            
            # 滚动到底部
            await self._scroll_to_bottom(scroll_amount=self.scroll_config.scroll_amount)
            
            # 等待新内容加载
            await asyncio.sleep(scroll_delay)
            
            # 回调通知
            if callback:
                callback(loaded_pages, current_height)
            
            logger.debug(f"已加载 {loaded_pages} 页，当前高度: {current_height}")
        
        logger.info(f"无限滚动加载完成，共加载 {loaded_pages} 页")
        return loaded_pages
    
    async def scroll_until_not_found(
        self,
        selector: str,
        max_pages: int = 20,
        scroll_delay: float = 0.5
    ) -> int:
        """
        滚动直到元素不再出现
        
        适用于：列表页滚动加载，直到没有新元素
        
        Args:
            selector: 元素选择器
            max_pages: 最大滚动页数
            scroll_delay: 每次滚动后的等待时间
        
        Returns:
            int: 找到的元素总数
        """
        all_elements = []
        previous_count = 0
        
        logger.info(f"开始滚动查找元素: {selector}")
        
        for page in range(max_pages):
            # 获取当前可见元素
            elements = await self._get_elements(selector)
            
            # 去重（基于元素文本或 ID）
            unique_elements = self._deduplicate_elements(elements, all_elements)
            all_elements.extend(unique_elements)
            
            logger.debug(f"第 {page + 1} 页，新增 {len(unique_elements)} 个元素，总计 {len(all_elements)} 个")
            
            # 如果没有新元素，停止
            if len(unique_elements) == 0:
                logger.info(f"未找到新元素，停止滚动")
                break
            
            previous_count = len(all_elements)
            
            # 滚动
            await self._scroll_to_bottom()
            await asyncio.sleep(scroll_delay)
        
        logger.info(f"滚动查找完成，共找到 {len(all_elements)} 个元素")
        return len(all_elements)
    
    # =========================================================================
    # 懒加载处理
    # =========================================================================
    
    async def wait_for_lazy_images(
        self,
        selector: str = None,
        timeout: float = None
    ) -> bool:
        """
        等待懒加载图片加载完成
        
        Args:
            selector: 懒加载图片选择器
            timeout: 超时时间
        
        Returns:
            bool: 是否所有懒加载图片都已加载
        """
        selector = selector or self.lazy_config.selector
        timeout = timeout or self.lazy_config.timeout
        
        logger.info(f"等待懒加载图片完成: {selector}")
        
        deadline = asyncio.get_event_loop().time() + timeout
        
        while asyncio.get_event_loop().time() < deadline:
            pending = await self._count_lazy_images(selector)
            
            if pending == 0:
                logger.info("所有懒加载图片已加载完成")
                return True
            
            logger.debug(f"还有 {pending} 个懒加载图片待加载")
            await asyncio.sleep(self.lazy_config.check_interval)
        
        logger.warning(f"等待懒加载图片超时 ({timeout}s)")
        return False
    
    async def load_lazy_images(self, selector: str = None) -> int:
        """
        主动加载懒加载图片
        
        通过修改 data-src 属性触发图片加载
        
        Returns:
            int: 加载的图片数量
        """
        selector = selector or self.lazy_config.selector
        
        js = f"""
        (() => {{
            const imgs = document.querySelectorAll('{selector}');
            let loaded = 0;
            imgs.forEach(img => {{
                const src = img.dataset.src || img.dataset.lazy || img.getAttribute('data-src');
                if (src && !img.src) {{
                    img.src = src;
                    loaded++;
                }}
            }});
            return loaded;
        }})()
        """
        
        count = await self.session.eval_js(js)
        logger.info(f"触发了 {count} 个懒加载图片")
        return count
    
    # =========================================================================
    # 虚拟列表处理
    # =========================================================================
    
    async def collect_virtual_list(
        self,
        container_selector: str,
        item_selector: str,
        max_items: int = 100,
        scroll_delay: float = 0.3
    ) -> List[str]:
        """
        收集虚拟列表中的所有元素
        
        虚拟列表只渲染可见区域的内容，需要滚动收集
        
        Args:
            container_selector: 容器选择器
            item_selector: 列表项选择器
            max_items: 最大收集数量
            scroll_delay: 每次滚动后的等待时间
        
        Returns:
            List[str]: 收集到的元素文本列表
        """
        items = []
        seen_texts = set()
        
        logger.info(f"开始收集虚拟列表，最大 {max_items} 项")
        
        for _ in range(max_items):
            # 获取当前可见元素
            visible_items = await self._get_virtual_list_items(container_selector, item_selector)
            
            # 去重
            new_items = []
            for item in visible_items:
                if item not in seen_texts:
                    seen_texts.add(item)
                    new_items.append(item)
            
            items.extend(new_items)
            
            logger.debug(f"已收集 {len(items)} 个唯一项")
            
            if len(items) >= max_items:
                break
            
            # 滚动到下一个可见项
            await self._scroll_virtual_list_item(container_selector)
            await asyncio.sleep(scroll_delay)
        
        logger.info(f"虚拟列表收集完成，共 {len(items)} 项")
        return items[:max_items]
    
    # =========================================================================
    # 辅助方法
    # =========================================================================
    
    async def _get_scroll_height(self) -> int:
        """获取页面滚动高度"""
        return await self.session.eval_js("document.documentElement.scrollHeight")
    
    async def _scroll_to_bottom(self, scroll_amount: int = None):
        """滚动到页面底部"""
        scroll_amount = scroll_amount or self.scroll_config.scroll_amount
        await self.session.eval_js(f"window.scrollBy(0, {scroll_amount})")
    
    async def _get_elements(self, selector: str) -> List[dict]:
        """获取匹配选择器的元素"""
        js = f"""
        (() => {{
            return Array.from(document.querySelectorAll('{selector}')).map(el => ({{
                text: (el.innerText || '').trim().slice(0, 200),
                tag: el.tagName.toLowerCase(),
                id: el.id || null,
                class: el.className || null,
            }}));
        }})()
        """
        return await self.session.eval_js(js) or []
    
    def _deduplicate_elements(
        self,
        new_elements: List[dict],
        existing_elements: List[dict]
    ) -> List[dict]:
        """去重元素（基于文本、ID 或 class）"""
        existing_keys = set()
        for el in existing_elements:
            key = el.get('text') or el.get('id') or el.get('class')
            if key:
                existing_keys.add(key)
        
        unique = []
        for el in new_elements:
            key = el.get('text') or el.get('id') or el.get('class')
            if key and key not in existing_keys:
                unique.append(el)
        
        return unique
    
    async def _count_lazy_images(self, selector: str) -> int:
        """统计未加载的懒加载图片数量"""
        js = f"""
        (() => {{
            const imgs = document.querySelectorAll('{selector}');
            let pending = 0;
            imgs.forEach(img => {{
                const src = img.dataset.src || img.dataset.lazy || img.getAttribute('data-src');
                if (src && !img.complete) {{
                    pending++;
                }}
            }});
            return pending;
        }})()
        """
        return await self.session.eval_js(js) or 0
    
    async def _get_virtual_list_items(
        self,
        container_selector: str,
        item_selector: str
    ) -> List[str]:
        """获取虚拟列表当前可见项"""
        js = f"""
        (() => {{
            const container = document.querySelector('{container_selector}');
            if (!container) return [];
            
            const items = container.querySelectorAll('{item_selector}');
            return Array.from(items).map(el => (el.innerText || '').trim()).filter(t => t);
        }})()
        """
        return await self.session.eval_js(js) or []
    
    async def _scroll_virtual_list_item(self, container_selector: str):
        """滚动虚拟列表到下一项"""
        js = f"""
        (() => {{
            const container = document.querySelector('{container_selector}');
            if (!container) return;
            
            const items = container.querySelectorAll('[class*="item"], [role="option"]');
            if (items.length > 0) {{
                items[0].scrollIntoView({{block: "center"}});
            }}
        }})()
        """
        await self.session.eval_js(js)
