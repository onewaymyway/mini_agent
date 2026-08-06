"""
infinite_scroll.py - 无限滚动加载模块

支持：
- 自动无限滚动加载
- 滚动位置检测
- 加载完成检测
- 虚拟列表处理
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable

logger = logging.getLogger(__name__)


@dataclass
class ScrollState:
    """滚动状态"""
    current_position: float = 0.0
    max_position: float = 0.0
    scroll_count: int = 0
    is_loading: bool = False
    content_height: float = 0.0
    viewport_height: float = 0.0
    
    def to_dict(self) -> dict:
        return {
            "current_position": self.current_position,
            "max_position": self.max_position,
            "scroll_count": self.scroll_count,
            "is_loading": self.is_loading,
            "content_height": self.content_height,
            "viewport_height": self.viewport_height,
        }


class InfiniteScrollHandler:
    """
    无限滚动处理器
    
    支持自动滚动加载更多内容，适用于小红书、抖音、微博等无限滚动页面。
    """
    
    def __init__(self, session, config: Optional[Dict[str, Any]] = None):
        self.session = session
        self.config = config or {
            "scroll_step": 500,  # 每次滚动距离
            "scroll_delay": (0.5, 1.5),  # 滚动延迟范围
            "load_wait": 2.0,  # 等待加载时间
            "max_scrolls": 20,  # 最大滚动次数
            "stability_check": True,  # 稳定性检查
            "stability_threshold": 3,  # 连续稳定次数
        }
        self._scroll_history: List[Dict[str, Any]] = []
        self._content_observer = None
    
    def scroll_to_bottom(self, max_scrolls: int = None) -> int:
        """
        滚动到页面底部
        
        Args:
            max_scrolls: 最大滚动次数
        
        Returns:
            实际滚动次数
        """
        max_scrolls = max_scrolls or self.config["max_scrolls"]
        scroll_count = 0
        
        for i in range(max_scrolls):
            # 检查是否已到底部
            if self._is_at_bottom():
                logger.info(f"已到达页面底部，停止滚动")
                break
            
            # 执行滚动
            self._scroll_step()
            scroll_count += 1
            
            # 等待内容加载
            self._wait_for_load()
            
            # 记录历史
            self._scroll_history.append({
                "scroll_count": scroll_count,
                "position": self._get_scroll_position(),
                "timestamp": time.time(),
            })
            
            logger.debug(f"已滚动 {scroll_count} 次，当前位置: {self._get_scroll_position()}")
        
        logger.info(f"滚动完成，共滚动 {scroll_count} 次")
        return scroll_count
    
    def scroll_to_element(self, selector: str, offset: float = 0) -> bool:
        """
        滚动到指定元素
        
        Args:
            selector: 元素选择器
            offset: 偏移量
        
        Returns:
            是否成功
        """
        js = f'''
        (function() {{
            var el = document.querySelector({selector!r});
            if (!el) return false;
            
            var rect = el.getBoundingClientRect();
            var scrollBy = rect.top + window.pageYOffset - {offset!r};
            window.scrollTo(0, scrollBy);
            return true;
        }})()
        '''
        
        try:
            result = self.session.eval_js(js)
            return bool(result)
        except Exception as e:
            logger.error(f"滚动到元素失败: {e}")
            return False
    
    def scroll_and_collect(self, collector: Callable, max_scrolls: int = None) -> List[Any]:
        """
        滚动并收集内容
        
        Args:
            collector: 收集函数，接收当前页面内容
            max_scrolls: 最大滚动次数
        
        Returns:
            收集的内容列表
        """
        collected = []
        
        for i in range(max_scrolls or self.config["max_scrolls"]):
            # 收集当前内容
            content = collector(self.session)
            if content:
                collected.extend(content)
            
            # 检查是否已到底部
            if self._is_at_bottom():
                break
            
            # 滚动
            self._scroll_step()
            self._wait_for_load()
        
        logger.info(f"收集完成，共收集 {len(collected)} 条内容")
        return collected
    
    def wait_for_content_load(self, timeout: float = 30.0, check_interval: float = 1.0) -> bool:
        """
        等待内容加载完成
        
        Args:
            timeout: 超时时间
            check_interval: 检查间隔
        
        Returns:
            是否成功
        """
        start_time = time.time()
        last_height = 0
        stable_count = 0
        
        while time.time() - start_time < timeout:
            current_height = self._get_content_height()
            
            # 检查内容是否稳定
            if current_height == last_height:
                stable_count += 1
                if stable_count >= self.config["stability_threshold"]:
                    logger.info("内容加载完成，已稳定")
                    return True
            else:
                stable_count = 0
            
            last_height = current_height
            time.sleep(check_interval)
        
        logger.warning(f"等待内容加载超时 ({timeout}s)")
        return False
    
    def _scroll_step(self):
        """执行一次滚动"""
        import random
        step = self.config["scroll_step"]
        
        js = f'''
        (function() {{
            window.scrollBy(0, {step});
            return window.pageYOffset;
        }})()
        '''
        
        try:
            self.session.eval_js(js)
        except Exception as e:
            logger.error(f"滚动失败: {e}")
    
    def _wait_for_load(self):
        """等待内容加载"""
        import random
        delay = random.uniform(*self.config["scroll_delay"])
        time.sleep(delay)
    
    def _is_at_bottom(self) -> bool:
        """检查是否已到底部"""
        js = '''
        (function() {
            var scrollTop = window.pageYOffset || document.documentElement.scrollTop;
            var windowHeight = window.innerHeight || document.documentElement.clientHeight;
            var documentHeight = document.documentElement.scrollHeight;
            
            return (scrollTop + windowHeight) >= (documentHeight - 100);
        })()
        '''
        
        try:
            return self.session.eval_js(js)
        except Exception:
            return False
    
    def _get_scroll_position(self) -> float:
        """获取当前滚动位置"""
        try:
            result = self.session.eval_js("window.pageYOffset")
            return float(result) if result else 0.0
        except Exception:
            return 0.0
    
    def _get_content_height(self) -> float:
        """获取内容高度"""
        try:
            result = self.session.eval_js("document.documentElement.scrollHeight")
            return float(result) if result else 0.0
        except Exception:
            return 0.0
    
    def get_scroll_state(self) -> ScrollState:
        """获取当前滚动状态"""
        return ScrollState(
            current_position=self._get_scroll_position(),
            max_position=self._get_content_height(),
            scroll_count=len(self._scroll_history),
            is_loading=self._is_loading(),
        )
    
    def _is_loading(self) -> bool:
        """检查是否正在加载"""
        js = '''
        (function() {
            // 检查常见的加载指示器
            var loaders = document.querySelectorAll(
                '.loading, .spinner, .loader, [class*="loading"], [class*="spinner"]'
            );
            return loaders.length > 0;
        })()
        '''
        try:
            return self.session.eval_js(js)
        except Exception:
            return False
    
    def get_scroll_history(self) -> List[Dict[str, Any]]:
        """获取滚动历史"""
        return self._scroll_history.copy()
    
    def clear_history(self):
        """清空滚动历史"""
        self._scroll_history.clear()


# 便捷函数
def create_infinite_scroll_handler(session, config: Optional[Dict[str, Any]] = None) -> InfiniteScrollHandler:
    """创建无限滚动处理器"""
    return InfiniteScrollHandler(session, config)


def scroll_to_bottom(session, max_scrolls: int = 20) -> int:
    """滚动到页面底部"""
    handler = create_infinite_scroll_handler(session)
    return handler.scroll_to_bottom(max_scrolls)


def scroll_and_collect(session, collector: Callable, max_scrolls: int = 20) -> List[Any]:
    """滚动并收集内容"""
    handler = create_infinite_scroll_handler(session)
    return handler.scroll_and_collect(collector, max_scrolls)
