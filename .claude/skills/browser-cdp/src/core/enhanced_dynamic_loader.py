"""
enhanced_dynamic_loader.py - 增强版动态内容加载器

支持：
1. 智能无限滚动（自动检测容器、高度变化判断）
2. 虚拟列表处理（分段滚动、去重收集）
3. 懒加载图片等待
4. 动态内容稳定性检测
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ScrollResult:
    """滚动结果"""
    success: bool
    pages_loaded: int = 0
    items_found: int = 0
    total_height: int = 0
    errors: List[str] = field(default_factory=list)


@dataclass
class ScrollConfig:
    """滚动配置"""
    # 容器选择器
    container_selector: str = ""
    # 最大滚动页数
    max_pages: int = 10
    # 每次滚动距离（像素）
    scroll_distance: int = 800
    # 滚动间隔（秒）
    scroll_delay: float = 0.8
    # 高度变化阈值（像素）
    height_threshold: int = 100
    # 列表项选择器
    item_selector: str = ""
    # 加载指示器选择器
    loader_selector: str = ".loading, .load-more, [class*='loading'], [class*='skeleton']"
    # 底部指示器选择器
    bottom_selector: str = ".end-of-list, [class*='no-more'], .finished, [class*='end']"
    # 是否启用智能检测
    smart_detect: bool = True
    # 滚动风格：natural/pause/erratic
    scroll_style: str = "natural"


class EnhancedDynamicLoader:
    """
    增强版动态内容加载器
    
    支持：
    1. 智能无限滚动
    2. 虚拟列表处理
    3. 懒加载等待
    4. 动态内容稳定性检测
    """
    
    def __init__(self, session, config: Optional[ScrollConfig] = None):
        self.session = session
        self.config = config or ScrollConfig()
        self._scroll_history: List[Dict] = []
    
    # =========================================================================
    # 智能无限滚动
    # =========================================================================
    
    async def smart_scroll(
        self,
        max_pages: int = None,
        stop_condition: Callable[[int, int], bool] = None,
        callback: Callable[[int, int], None] = None,
    ) -> ScrollResult:
        """
        智能无限滚动加载
        
        特性：
        - 自动检测滚动容器
        - 根据内容变化调整滚动策略
        - 支持自定义停止条件
        
        Args:
            max_pages: 最大滚动页数
            stop_condition: 停止条件函数 (pages_loaded, items_count) -> bool
            callback: 每页加载后的回调 (pages_loaded, items_count)
        
        Returns:
            ScrollResult: 滚动结果
        """
        max_pages = max_pages or self.config.max_pages
        
        logger.info(f"开始智能滚动加载，最大页数: {max_pages}")
        
        # 1. 检测滚动容器
        container = await self._detect_scroll_container()
        
        # 2. 记录初始状态
        initial_height = await self._get_scroll_height(container)
        initial_items = await self._count_items()
        
        logger.debug(f"初始高度: {initial_height}, 初始项数: {initial_items}")
        
        # 3. 执行滚动
        pages_loaded = 0
        current_height = initial_height
        current_items = initial_items
        
        for page in range(max_pages):
            # 滚动
            scrolled = await self._scroll_page(container)
            if not scrolled:
                logger.warning("滚动失败")
                self._scroll_history.append({"page": page, "success": False, "error": "scroll_failed"})
                break
            
            # 等待内容加载
            await asyncio.sleep(self.config.scroll_delay)
            
            # 检查内容变化
            new_height = await self._get_scroll_height(container)
            new_items = await self._count_items()
            
            height_change = new_height - current_height
            items_change = new_items - current_items
            
            logger.debug(f"第 {page + 1} 页: 高度变化 {height_change}px, 项数变化 {items_change}")
            
            # 检查是否还有新内容
            if height_change < self.config.height_threshold and items_change == 0:
                logger.info(f"检测到内容无变化，停止滚动（第 {page + 1} 页）")
                break
            
            pages_loaded += 1
            current_height = new_height
            current_items = new_items
            
            self._scroll_history.append({
                "page": page + 1,
                "success": True,
                "height_change": height_change,
                "items_change": items_change,
            })
            
            # 调用回调
            if callback:
                callback(pages_loaded, current_items)
            
            # 检查停止条件
            if stop_condition and stop_condition(pages_loaded, current_items):
                logger.info(f"满足停止条件，停止滚动")
                break
        
        result = ScrollResult(
            success=True,
            pages_loaded=pages_loaded,
            items_found=current_items,
            total_height=current_height,
        )
        
        logger.info(f"智能滚动完成: {pages_loaded} 页, {current_items} 项")
        return result
    
    async def _detect_scroll_container(self) -> str:
        """检测滚动容器"""
        # 尝试常见容器选择器
        containers = [
            "main",
            "[role='main']",
            ".content",
            ".main-content",
            ".feed",
            ".timeline",
            ".list-container",
            "body",
        ]
        
        for selector in containers:
            try:
                has_scroll = await self.session.eval_js(f"""
                    () => {{
                        const el = document.querySelector({selector!r});
                        if (!el) return false;
                        return el.scrollHeight > el.clientHeight;
                    }}
                """)
                if has_scroll:
                    logger.debug(f"检测到滚动容器: {selector}")
                    return selector
            except Exception:
                continue
        
        logger.debug("未检测到特定容器，使用 body")
        return "body"
    
    async def _get_scroll_height(self, selector: str = "") -> int:
        """获取滚动高度"""
        if selector and selector != "body":
            try:
                height = await self.session.eval_js(f"""
                    () => {{
                        const el = document.querySelector({selector!r});
                        return el ? el.scrollHeight : 0;
                    }}
                """)
                return int(height)
            except Exception:
                pass
        
        return await self.session.eval_js("() => document.documentElement.scrollHeight")
    
    async def _count_items(self) -> int:
        """统计当前可见项数"""
        if not self.config.item_selector:
            return 0
        
        try:
            count = await self.session.eval_js(f"""
                () => {{
                    const items = document.querySelectorAll({self.config.item_selector!r});
                    return items.length;
                }}
            """)
            return int(count)
        except Exception:
            return 0
    
    async def _scroll_page(self, container: str = "") -> bool:
        """执行一次滚动"""
        try:
            if container and container != "body":
                await self.session.eval_js(f"""
                    () => {{
                        const el = document.querySelector({container!r});
                        if (el) {{
                            el.scrollTop += {self.config.scroll_distance};
                        }} else {{
                            window.scrollBy(0, {self.config.scroll_distance});
                        }}
                    }}
                """)
            else:
                await self.session.eval_js(f"""
                    () => {{
                        window.scrollBy(0, {self.config.scroll_distance});
                    }}
                """)
            return True
        except Exception as e:
            logger.error(f"滚动失败: {e}")
            return False
    
    # =========================================================================
    # 虚拟列表处理
    # =========================================================================
    
    async def load_virtual_list(
        self,
        item_selector: str,
        max_items: int = 100,
        scroll_distance: int = None,
    ) -> List[Dict[str, Any]]:
        """
        加载虚拟列表所有数据
        
        Args:
            item_selector: 列表项选择器
            max_items: 最大收集项数
            scroll_distance: 每次滚动距离
        
        Returns:
            List[Dict]: 收集的数据列表
        """
        scroll_distance = scroll_distance or self.config.scroll_distance
        self.config.item_selector = item_selector
        
        logger.info(f"开始加载虚拟列表，最大项数: {max_items}")
        
        all_items = []
        seen_keys = set()
        current_pos = 0
        
        # 检测虚拟列表特征
        is_virtual = await self._detect_virtual_list(item_selector)
        
        if is_virtual:
            logger.info("检测到虚拟列表，使用分段滚动策略")
            scroll_step = await self._get_virtual_scroll_step(item_selector)
        else:
            logger.info("检测到普通列表，使用标准滚动策略")
            scroll_step = scroll_distance
        
        while len(all_items) < max_items:
            # 滚动到指定位置
            await self._scroll_to_position(current_pos)
            await asyncio.sleep(0.5)
            
            # 收集当前可见项
            items = await self._collect_visible_items(item_selector)
            
            # 去重
            new_items = self._deduplicate_items(items, seen_keys)
            all_items.extend(new_items)
            seen_keys.update(self._get_item_keys(items))
            
            logger.debug(f"已收集 {len(all_items)} 项，本次新增 {len(new_items)} 项")
            
            # 检查是否还有更多
            if not await self._has_more_items(item_selector, len(all_items)):
                break
            
            current_pos += scroll_step
        
        logger.info(f"虚拟列表加载完成，共 {len(all_items)} 项")
        return all_items
    
    async def _detect_virtual_list(self, item_selector: str) -> bool:
        """检测是否为虚拟列表"""
        try:
            # 检查是否有虚拟列表特征
            is_virtual = await self.session.eval_js(f"""
                () => {{
                    const items = document.querySelectorAll({item_selector!r});
                    if (items.length === 0) return false;
                    
                    // 检查是否有滚动容器
                    const container = items[0].closest('[class*="virtual"], [class*="scroll"], [class*="list"]');
                    if (container) {{
                        return container.scrollHeight > container.clientHeight;
                    }}
                    
                    // 检查项数是否异常少（虚拟列表通常只渲染可见区域）
                    return items.length < 20;
                }}
            """)
            return is_virtual
        except Exception:
            return False
    
    async def _get_virtual_scroll_step(self, item_selector: str) -> int:
        """获取虚拟列表滚动步长"""
        try:
            step = await self.session.eval_js(f"""
                () => {{
                    const items = document.querySelectorAll({item_selector!r});
                    if (items.length < 2) return 500;
                    
                    // 计算相邻项的平均高度
                    let totalHeight = 0;
                    for (let i = 1; i < Math.min(items.length, 5); i++) {{
                        totalHeight += items[i].getBoundingClientRect().height;
                    }}
                    return Math.max(100, Math.round(totalHeight / (Math.min(items.length, 5) - 1)));
                }}
            """)
            return int(step)
        except Exception:
            return 500
    
    async def _scroll_to_position(self, position: int):
        """滚动到指定位置"""
        await self.session.eval_js(f"""
            () => {{
                window.scrollTo(0, {position});
            }}
        """)
    
    async def _collect_visible_items(self, item_selector: str) -> List[Dict]:
        """收集当前可见的列表项"""
        try:
            items_data = await self.session.eval_js(f"""
                () => {{
                    const items = document.querySelectorAll({item_selector!r});
                    const result = [];
                    
                    items.forEach((item, index) => {{
                        const rect = item.getBoundingClientRect();
                        const isVisible = rect.top < window.innerHeight && rect.bottom > 0;
                        
                        if (isVisible) {{
                            result.push({{
                                index: index,
                                text: item.innerText?.substring(0, 200) || '',
                                id: item.getAttribute('data-id') || item.getAttribute('id') || '',
                                class: item.className || '',
                            }});
                        }}
                    }});
                    
                    return result;
                }}
            """)
            return items_data or []
        except Exception as e:
            logger.error(f"收集列表项失败: {e}")
            return []
    
    def _deduplicate_items(self, items: List[Dict], seen_keys: set) -> List[Dict]:
        """去重列表项"""
        new_items = []
        for item in items:
            key = item.get('id') or item.get('text', '')[:50]
            if key and key not in seen_keys:
                seen_keys.add(key)
                new_items.append(item)
        return new_items
    
    def _get_item_keys(self, items: List[Dict]) -> set:
        """获取列表项的唯一键"""
        return {item.get('id') or item.get('text', '')[:50] for item in items if item.get('id') or item.get('text')}
    
    async def _has_more_items(self, item_selector: str, current_count: int) -> bool:
        """检查是否还有更多项"""
        try:
            # 检查底部指示器
            has_bottom = await self.session.eval_js(f"""
                () => {{
                    const bottomSelectors = {self.config.bottom_selector!r};
                    const selectors = bottomSelectors.split(',');
                    for (const sel of selectors) {{
                        if (document.querySelector(sel.trim())) return false;
                    }}
                    return true;
                }}
            """)
            
            if not has_bottom:
                return False
            
            # 检查滚动位置
            scroll_info = await self.session.eval_js("""
                () => {{
                    const scrollHeight = document.documentElement.scrollHeight;
                    const windowHeight = window.innerHeight;
                    const scrollY = window.scrollY;
                    const remaining = scrollHeight - (scrollY + windowHeight);
                    return {{
                        remaining: remaining,
                        total: scrollHeight,
                        scrolled: scrollY,
                    }};
                }}
            """)
            
            remaining = scroll_info.get('remaining', 0)
            if remaining > 1000:
                return True
            
            return False
        except Exception:
            return True
    
    # =========================================================================
    # 懒加载图片等待
    # =========================================================================
    
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
        logger.info(f"等待懒加载图片完成，选择器: {selector}")
        
        deadline = time.time() + timeout
        loaded_count = 0
        
        while time.time() < deadline:
            try:
                result = await self.session.eval_js(f"""
                    () => {{
                        const images = document.querySelectorAll({selector!r});
                        let loaded = 0;
                        let total = images.length;
                        
                        images.forEach(img => {{
                            if (img.complete && img.naturalWidth > 0) {{
                                loaded++;
                            }}
                        }});
                        
                        window.__lazy_images_loaded = loaded;
                        window.__lazy_images_total = total;
                        
                        return {{ loaded, total }};
                    }}
                """)
                
                loaded_count = result.get('loaded', 0)
                total = result.get('total', 0)
                
                logger.debug(f"懒加载图片: {loaded_count}/{total} 已加载")
                
                if loaded_count == total and total > 0:
                    logger.info(f"所有懒加载图片已加载完成: {loaded_count}")
                    return loaded_count
                
                await asyncio.sleep(0.5)
            except Exception as e:
                logger.debug(f"检查懒加载图片失败: {e}")
                break
        
        logger.warning(f"懒加载图片等待超时，已加载: {loaded_count}")
        return loaded_count
    
    # =========================================================================
    # 工具方法
    # =========================================================================
    
    def get_scroll_stats(self) -> Dict:
        """获取滚动统计信息"""
        if not self._scroll_history:
            return {"total_pages": 0, "success_rate": 0.0}
        
        total = len(self._scroll_history)
        successes = sum(1 for h in self._scroll_history if h.get('success'))
        
        return {
            "total_pages": total,
            "success_pages": successes,
            "success_rate": successes / total if total > 0 else 0.0,
            "history": self._scroll_history[-10:],  # 最近 10 条
        }
    
    def clear_history(self):
        """清空滚动历史"""
        self._scroll_history.clear()


# 便捷函数
async def smart_scroll(
    session,
    max_pages: int = 10,
    item_selector: str = "",
    stop_condition: Callable = None,
) -> ScrollResult:
    """
    智能无限滚动的便捷函数
    
    Args:
        session: CDP session 对象
        max_pages: 最大滚动页数
        item_selector: 列表项选择器
        stop_condition: 停止条件
    
    Returns:
        ScrollResult: 滚动结果
    """
    config = ScrollConfig(item_selector=item_selector)
    loader = EnhancedDynamicLoader(session, config)
    return await loader.smart_scroll(max_pages=max_pages, stop_condition=stop_condition)


async def load_virtual_list_data(
    session,
    item_selector: str,
    max_items: int = 100,
) -> List[Dict]:
    """
    加载虚拟列表数据的便捷函数
    
    Args:
        session: CDP session 对象
        item_selector: 列表项选择器
        max_items: 最大收集项数
    
    Returns:
        List[Dict]: 收集的数据列表
    """
    config = ScrollConfig(item_selector=item_selector)
    loader = EnhancedDynamicLoader(session, config)
    return await loader.load_virtual_list(item_selector=item_selector, max_items=max_items)


async def wait_lazy_images(
    session,
    selector: str = "img[loading='lazy']",
    timeout: float = 10.0,
) -> int:
    """
    等待懒加载图片完成的便捷函数
    
    Args:
        session: CDP session 对象
        selector: 懒加载图片选择器
        timeout: 超时时间
    
    Returns:
        int: 已加载的图片数量
    """
    config = ScrollConfig()
    loader = EnhancedDynamicLoader(session, config)
    return await loader.wait_for_lazy_images(selector=selector, timeout=timeout)
