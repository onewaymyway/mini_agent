"""
dom_observer.py - DOM 变化监听模块

通过 MutationObserver 监听 DOM 变化，检测页面内容稳定性。
适用于 SPA 页面、动态加载内容等场景。
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Optional, Callable, List

logger = logging.getLogger(__name__)


@dataclass
class DOMChange:
    """DOM 变化记录"""
    timestamp: float
    change_type: str  # 'add', 'remove', 'modify'
    target: str  # CSS selector of changed element
    details: dict = None


class DOMObserver:
    """
    DOM 变化监听器
    
    使用 MutationObserver 监听 DOM 变化，提供：
    1. 页面内容稳定性检测
    2. 特定元素变化监听
    3. 变化历史记录
    """
    
    def __init__(self, session):
        self.session = session
        self._changes: List[DOMChange] = []
        self._max_history = 1000
        self._observer_id = None
        self._callbacks: List[Callable] = []
    
    async def observe(
        self,
        selector: str = "body",
        subtree: bool = True,
        child_list: bool = True,
        attributes: bool = True,
        character_data: bool = True,
        callback: Callable = None,
    ) -> str:
        """
        开始监听 DOM 变化
        
        Args:
            selector: 监听的选择器
            subtree: 是否监听子树
            child_list: 是否监听子节点变化
            attributes: 是否监听属性变化
            character_data: 是否监听文本变化
            callback: 变化回调函数
        
        Returns:
            str: 观察者 ID
        """
        if callback:
            self._callbacks.append(callback)
        
        # 注入 MutationObserver 脚本
        observer_script = f"""
        () => {{
            return new Promise((resolve) => {{
                const target = document.querySelector({selector!r});
                if (!target) {{
                    resolve(null);
                    return;
                }}
                
                const observer = new MutationObserver((mutations) => {{
                    window.__dom_observer_changes = window.__dom_observer_changes || [];
                    mutations.forEach((mutation) => {{
                        let type = 'modify';
                        if (mutation.type === 'childList') {{
                            type = mutation.addedNodes.length > 0 ? 'add' : 'remove';
                        }}
                        window.__dom_observer_changes.push({{
                            type: type,
                            target: mutation.target ? mutation.target.tagName : 'unknown',
                            timestamp: Date.now(),
                        }});
                    }});
                }});
                
                observer.observe(target, {{
                    subtree: {subtree},
                    childList: {child_list},
                    attributes: {attributes},
                    characterData: {character_data},
                }});
                
                window.__dom_observer_id = observer;
                resolve('observing');
            }});
        }}
        """
        
        result = await self.session.eval_js(observer_script)
        self._observer_id = f"observer_{int(time.time() * 1000)}"
        logger.info(f"DOM 观察者已启动: {self._observer_id}")
        return self._observer_id
    
    async def stop(self) -> bool:
        """停止 DOM 监听"""
        if not self._observer_id:
            return True
        
        try:
            await self.session.eval_js("""
                () => {
                    if (window.__dom_observer_id) {{
                        window.__dom_observer_id.disconnect();
                        window.__dom_observer_id = null;
                    }}
                    return true;
                }
            """)
            self._observer_id = None
            logger.info("DOM 观察者已停止")
            return True
        except Exception as e:
            logger.error(f"停止 DOM 观察者失败: {e}")
            return False
    
    async def get_changes(self, limit: int = 100) -> List[dict]:
        """获取最近的 DOM 变化记录"""
        try:
            changes = await self.session.eval_js("""
                () => {
                    return window.__dom_observer_changes || [];
                }
            """)
            
            # 转换为 DOMChange 对象
            self._changes = [
                DOMChange(
                    timestamp=c.get('timestamp', 0) / 1000,
                    change_type=c.get('type', 'modify'),
                    target=c.get('target', 'unknown'),
                )
                for c in changes[-limit:]
            ]
            
            return self._changes
        except Exception as e:
            logger.error(f"获取 DOM 变化失败: {e}")
            return []
    
    async def wait_for_stable(
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
        logger.info(f"开始等待 DOM 稳定，超时: {timeout}s")
        
        stable_iterations = 0
        last_change_count = -1
        
        deadline = time.time() + timeout
        
        while time.time() < deadline:
            changes = await self.get_changes(limit=50)
            change_count = len(changes)
            
            if change_count == last_change_count and change_count > 0:
                stable_iterations += 1
                logger.debug(f"DOM 稳定检测 #{stable_iterations}/{stable_count}")
                
                if stable_iterations >= stable_count:
                    logger.info("DOM 稳定检测通过")
                    return True
            else:
                stable_iterations = 0
                last_change_count = change_count
            
            await asyncio.sleep(check_interval)
        
        logger.warning(f"DOM 稳定检测超时 ({timeout}s)")
        return False
    
    async def wait_for_selector_appearance(
        self,
        selector: str,
        timeout: float = 10.0,
    ) -> bool:
        """
        等待特定选择器出现
        
        Args:
            selector: CSS 选择器
            timeout: 超时时间
        
        Returns:
            bool: 是否出现
        """
        logger.info(f"等待选择器出现: {selector}")
        
        deadline = time.time() + timeout
        
        while time.time() < deadline:
            result = await self.session.eval_js(f"""
                () => {{
                    return document.querySelector({selector!r}) !== null;
                }}
            """)
            
            if result:
                logger.info(f"选择器已出现: {selector}")
                return True
            
            await asyncio.sleep(0.3)
        
        logger.warning(f"选择器超时未出现: {selector}")
        return False
    
    async def wait_for_content_change(
        self,
        selector: str = "body",
        min_changes: int = 1,
        timeout: float = 15.0,
    ) -> bool:
        """
        等待内容变化
        
        Args:
            selector: 监听的元素
            min_changes: 最小变化次数
            timeout: 超时时间
        
        Returns:
            bool: 是否发生变化
        """
        logger.info(f"等待内容变化: {selector}")
        
        # 先获取初始变化数
        await self.get_changes()
        initial_count = len(self._changes)
        
        deadline = time.time() + timeout
        
        while time.time() < deadline:
            await asyncio.sleep(0.3)
            changes = await self.get_changes()
            
            if len(changes) - initial_count >= min_changes:
                logger.info(f"检测到 {len(changes) - initial_count} 次内容变化")
                return True
        
        logger.warning(f"内容变化检测超时 ({timeout}s)")
        return False
    
    def clear_history(self):
        """清空变化历史"""
        self._changes.clear()
        logger.debug("DOM 变化历史已清空")


# 便捷函数
async def observe_dom_changes(
    session,
    selector: str = "body",
    callback: Callable = None,
) -> str:
    """
    开始监听 DOM 变化的便捷函数
    
    Args:
        session: CDP session 对象
        selector: 监听的选择器
        callback: 变化回调
    
    Returns:
        str: 观察者 ID
    """
    observer = DOMObserver(session)
    return await observer.observe(selector=selector, callback=callback)


async def wait_for_dom_stable(
    session,
    check_interval: float = 0.5,
    stable_count: int = 3,
    timeout: float = 30.0,
) -> bool:
    """
    等待 DOM 稳定的便捷函数
    
    Args:
        session: CDP session 对象
        check_interval: 检查间隔
        stable_count: 连续稳定次数
        timeout: 超时时间
    
    Returns:
        bool: 是否稳定
    """
    observer = DOMObserver(session)
    await observer.observe()
    try:
        return await observer.wait_for_stable(
            check_interval=check_interval,
            stable_count=stable_count,
            timeout=timeout,
        )
    finally:
        await observer.stop()


async def wait_for_selector(
    session,
    selector: str,
    timeout: float = 10.0,
) -> bool:
    """
    等待选择器出现的便捷函数
    
    Args:
        session: CDP session 对象
        selector: CSS 选择器
        timeout: 超时时间
    
    Returns:
        bool: 是否出现
    """
    observer = DOMObserver(session)
    await observer.observe()
    try:
        return await observer.wait_for_selector_appearance(selector, timeout)
    finally:
        await observer.stop()
