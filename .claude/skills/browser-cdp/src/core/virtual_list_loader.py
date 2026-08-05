"""
virtual_list_loader.py - 虚拟列表加载器

支持虚拟列表（Virtual List）的自动滚动加载
适用于知乎、微博等使用虚拟滚动技术的网站
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from typing import Optional, List, Callable, Any

logger = logging.getLogger(__name__)


@dataclass
class ListItem:
    """列表项数据"""
    index: int
    text: str
    attributes: dict = None
    raw_element: Any = None
    
    def __post_init__(self):
        if self.attributes is None:
            self.attributes = {}


@dataclass
class VirtualListConfig:
    """虚拟列表配置"""
    # 容器选择器
    container_selector: str = ".virtual-list, [class*='virtual'], [class*='scroll-list'], .list-container"
    # 列表项选择器
    item_selector: str = ".list-item, [class*='list-item'], .item, [class*='item']"
    # 每次滚动距离（像素）
    scroll_distance: int = 500
    # 最大滚动次数
    max_iterations: int = 50
    # 滚动间隔（秒）
    scroll_interval: float = 0.3
    # 加载指示器选择器
    loader_selector: str = ".loading, .load-more, [class*='loading'], [class*='skeleton']"
    # 底部检测选择器
    bottom_selector: str = ".end-of-list, [class*='no-more'], .finished"
    # 提取数据的自定义函数（可选）
    extract_func: Optional[Callable] = None


class VirtualListLoader:
    """
    虚拟列表加载器
    
    自动滚动页面加载虚拟列表中的所有数据
    """
    
    def __init__(self, session, config: Optional[VirtualListConfig] = None):
        """
        Args:
            session: CDP session 对象
            config: 虚拟列表配置
        """
        self.session = session
        self.config = config or VirtualListConfig()
        self._all_items: List[ListItem] = []
        self._seen_indices: set = set()
        self._scroll_count: int = 0
    
    async def load_all(self) -> List[ListItem]:
        """
        加载虚拟列表所有数据
        
        Returns:
            List[ListItem]: 所有列表项
        """
        logger.info("开始加载虚拟列表...")
        
        # 1. 等待页面加载完成
        await self.session.wait_for_network_idle(timeout=10)
        
        # 2. 滚动加载
        for i in range(self.config.max_iterations):
            self._scroll_count = i
            
            # 获取当前可见项
            current_items = await self._get_visible_items()
            
            # 去重并添加新项
            new_items = self._deduplicate_items(current_items)
            self._all_items.extend(new_items)
            
            logger.debug(f"已加载 {len(self._all_items)} 项，本次新增 {len(new_items)} 项")
            
            # 检查是否还有更多数据
            if not await self._has_more():
                logger.info(f"虚拟列表加载完成，共 {len(self._all_items)} 项")
                break
            
            # 滚动加载更多
            if not await self._scroll_to_load_more():
                logger.warning("无法继续滚动，可能已到达底部")
                break
            
            # 等待加载
            await asyncio.sleep(self.config.scroll_interval)
        
        return self._all_items
    
    async def _get_visible_items(self) -> List[ListItem]:
        """获取当前可见的列表项"""
        try:
            elements = await self.session.query_selector_all(self.config.item_selector)
            items = []
            
            for idx, elem in enumerate(elements):
                try:
                    # 获取元素文本
                    text = await elem.inner_text()
                    
                    # 获取元素属性
                    attributes = {}
                    for attr in ['data-id', 'data-index', 'class', 'id']:
                        value = await elem.get_attribute(attr)
                        if value:
                            attributes[attr] = value
                    
                    # 使用自定义提取函数（如果有）
                    if self.config.extract_func:
                        item_data = self.config.extract_func(elem)
                        if item_data:
                            items.append(ListItem(
                                index=len(self._all_items) + len(items),
                                text=item_data.get('text', text),
                                attributes=item_data.get('attributes', attributes),
                                raw_element=elem
                            ))
                        continue
                    
                    items.append(ListItem(
                        index=len(self._all_items) + len(items),
                        text=text.strip(),
                        attributes=attributes,
                        raw_element=elem
                    ))
                except Exception as e:
                    logger.debug(f"提取列表项失败: {e}")
                    continue
            
            return items
        except Exception as e:
            logger.error(f"获取可见列表项失败: {e}")
            return []
    
    def _deduplicate_items(self, items: List[ListItem]) -> List[ListItem]:
        """去重列表项"""
        new_items = []
        for item in items:
            # 使用文本和前几个字符作为唯一标识
            key = f"{item.text[:50]}_{item.attributes.get('data-id', '')}"
            if key not in self._seen_indices:
                self._seen_indices.add(key)
                new_items.append(item)
        return new_items
    
    async def _has_more(self) -> bool:
        """检查是否还有更多数据"""
        try:
            # 检查底部指示器
            bottom_elements = await self.session.query_selector_all(self.config.bottom_selector)
            if bottom_elements:
                logger.debug("检测到列表底部指示器")
                return False
            
            # 检查加载指示器
            loader_elements = await self.session.query_selector_all(self.config.loader_selector)
            if loader_elements:
                # 如果正在加载，返回 True
                return True
            
            # 检查滚动位置
            scroll_height = await self.session.eval_js("() => document.documentElement.scrollHeight")
            window_height = await self.session.eval_js("() => window.innerHeight")
            scroll_offset = await self.session.eval_js("() => window.scrollY")
            
            # 如果距离底部还有很大距离，说明还有更多数据
            remaining = scroll_height - (scroll_offset + window_height)
            if remaining > 1000:
                return True
            
            return False
        except Exception as e:
            logger.debug(f"检查是否有更多数据失败: {e}")
            return True
    
    async def _scroll_to_load_more(self) -> bool:
        """滚动加载更多数据"""
        try:
            # 使用 JavaScript 滚动
            await self.session.eval_js(f"""
                () => {{
                    window.scrollBy(0, {self.config.scroll_distance});
                }}
            """)
            return True
        except Exception as e:
            logger.error(f"滚动失败: {e}")
            return False
    
    def get_items(self) -> List[ListItem]:
        """获取已加载的列表项"""
        return self._all_items
    
    def get_stats(self) -> dict:
        """获取加载统计信息"""
        return {
            "total_items": len(self._all_items),
            "scroll_count": self._scroll_count,
            "unique_items": len(self._seen_indices),
        }


# 便捷函数
async def load_virtual_list(session, config: Optional[VirtualListConfig] = None) -> List[ListItem]:
    """
    加载虚拟列表的便捷函数
    
    Args:
        session: CDP session 对象
        config: 虚拟列表配置
    
    Returns:
        List[ListItem]: 所有列表项
    """
    loader = VirtualListLoader(session, config)
    return await loader.load_all()


async def load_zhihu_answers(session, question_id: str) -> List[ListItem]:
    """
    加载知乎问题答案（虚拟列表示例）
    
    Args:
        session: CDP session 对象
        question_id: 问题 ID
    
    Returns:
        List[ListItem]: 答案列表
    """
    config = VirtualListConfig(
        container_selector=f".QuestionAnswer-item",
        item_selector=".RichContent-inner",
        scroll_distance=800,
        max_iterations=30,
    )
    
    loader = VirtualListLoader(session, config)
    return await loader.load_all()


async def load_weibo_timeline(session) -> List[ListItem]:
    """
    加载微博时间线（虚拟列表示例）
    
    Args:
        session: CDP session 对象
    
    Returns:
        List[ListItem]: 微博列表
    """
    config = VirtualListConfig(
        container_selector=".weibo-list",
        item_selector=".feed_item",
        scroll_distance=600,
        max_iterations=50,
    )
    
    loader = VirtualListLoader(session, config)
    return await loader.load_all()
