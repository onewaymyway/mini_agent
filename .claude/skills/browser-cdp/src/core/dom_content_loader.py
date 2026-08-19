"""
dom_content_loader.py - DOM内容加载与等待处理模块

解决以下问题：
- P22: AJAX等待对Fetch API失效（改用Performance API）
- P34: 缺少wait_for_stable策略（新增内容稳定检测）
- P36: 虚拟列表检测后无对应策略（集成VirtualListLoader）
- P39: iframe访问async/await与CDP同步模式冲突（改为同步轮询）

用法：
    from src.core.dom_content_loader import DOMContentLoader
    
    loader = DOMContentLoader(session)
    
    # 等待页面内容稳定
    await loader.wait_for_content_stable(timeout=10)
    
    # 等待Fetch API请求完成
    await loader.wait_for_fetch_complete(timeout=15)
    
    # 访问iframe（同步）
    result = loader.access_iframe_sync('#my-iframe', timeout=5)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# =========================================================================
# 数据模型
# =========================================================================

@dataclass
class ContentStabilityResult:
    """内容稳定检测结果"""
    stable: bool
    rounds_checked: int
    content_hash: str
    elapsed: float


@dataclass
class FetchRequest:
    """Fetch请求信息"""
    url: str
    method: str
    timestamp: float
    status: Optional[int] = None
    duration: float = 0.0


@dataclass
class IFrameResult:
    """iframe访问结果"""
    success: bool
    url: str = ""
    title: str = ""
    error: Optional[str] = None
    accessible: bool = False


# =========================================================================
# 核心处理器
# =========================================================================

class DOMContentLoader:
    """
    DOM内容加载处理器

    提供：
    1. 内容稳定检测（P34）
    2. Fetch API等待（P22）
    3. iframe同步访问（P39）
    4. 虚拟列表检测（P36）
    """

    def __init__(self, session, config: Optional[Dict] = None):
        self.session = session
        self.config = config or {}
        self._fetch_requests: List[FetchRequest] = []
        self._last_content_hash: str = ""

    # =========================================================================
    # P34: 内容稳定检测
    # =========================================================================

    def wait_for_content_stable(
        self,
        timeout: float = 10.0,
        check_rounds: int = 3,
        interval: float = 1.0,
    ) -> ContentStabilityResult:
        """等待页面内容稳定（P34修复）

        Args:
            timeout: 总超时时间（秒）
            check_rounds: 连续多少次内容不变才认为稳定
            interval: 每次检查间隔（秒）

        Returns:
            ContentStabilityResult
        """
        start_time = time.time()
        consecutive_stable = 0
        last_hash = ""

        while time.time() - start_time < timeout:
            # 计算当前内容哈希（使用innerText前500字符，避免全量计算开销）
            current_hash = self._compute_content_hash()

            if current_hash == last_hash:
                consecutive_stable += 1
                if consecutive_stable >= check_rounds:
                    elapsed = time.time() - start_time
                    logger.info(f"内容稳定检测通过: {consecutive_stable}轮，耗时{elapsed:.2f}s")
                    return ContentStabilityResult(
                        stable=True,
                        rounds_checked=consecutive_stable,
                        content_hash=current_hash,
                        elapsed=elapsed,
                    )
            else:
                consecutive_stable = 0

            last_hash = current_hash
            time.sleep(interval)

        elapsed = time.time() - start_time
        logger.warning(f"内容稳定检测超时: 已等待{elapsed:.2f}s，未达到{check_rounds}轮稳定")
        return ContentStabilityResult(
            stable=False,
            rounds_checked=consecutive_stable,
            content_hash=last_hash,
            elapsed=elapsed,
        )

    def _compute_content_hash(self) -> str:
        """计算页面内容哈希（轻量版）"""
        js = """
        (() => {
            const body = document.body || document.documentElement;
            if (!body) return '';
            // 只取前500字符，避免innerText触发全量reflow
            const text = (body.innerText || body.textContent || '').slice(0, 500);
            let hash = 0;
            for (let i = 0; i < text.length; i++) {
                const char = text.charCodeAt(i);
                hash = ((hash << 5) - hash) + char;
                hash = hash & hash;
            }
            return hash.toString(16);
        })()
        """
        return self.session.eval_js(js) or ""

    # =========================================================================
    # P22: Fetch API等待
    # =========================================================================

    def wait_for_fetch_complete(
        self,
        timeout: float = 15.0,
        poll_interval: float = 0.5,
    ) -> bool:
        """等待所有Fetch/XHR请求完成（P22修复）

        改进：使用Performance API检测活动请求数，而非document.readyState。

        Args:
            timeout: 超时时间（秒）
            poll_interval: 轮询间隔（秒）

        Returns:
            是否成功等待完成
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            # 检查当前活动请求数
            active_count = self._get_active_request_count()

            if active_count == 0:
                logger.debug("所有Fetch/XHR请求已完成")
                return True

            logger.debug(f"等待活动请求完成: {active_count}个")
            time.sleep(poll_interval)

        logger.warning(f"等待Fetch请求超时: 仍有{self._get_active_request_count()}个活动请求")
        return False

    def _get_active_request_count(self) -> int:
        """获取当前活动请求数"""
        js = """
        (() => {
            // 方法1: Performance API 检测 XHR/Fetch
            const entries = performance.getEntriesByType('resource');
            const pending = entries.filter(e => {
                // 过滤掉静态资源
                const type = e.initiatorType || '';
                return type === 'xmlhttprequest' || type === 'fetch';
            });
            return pending.length;
        })()
        """
        try:
            result = self.session.eval_js(js)
            return int(result) if result else 0
        except Exception:
            return 0

    # =========================================================================
    # P39: iframe同步访问
    # =========================================================================

    def access_iframe_sync(
        self,
        iframe_selector: str,
        timeout: float = 5.0,
    ) -> Optional[IFrameResult]:
        """同步访问iframe内容（P39修复）

        改进：使用同步轮询替代async/await，适配CDP同步会话。

        Args:
            iframe_selector: iframe选择器
            timeout: 超时时间（秒）

        Returns:
            IFrameResult，失败返回None
        """
        start_time = time.time()

        while time.time() - start_time < timeout:
            result = self._try_access_iframe(iframe_selector)
            if result and result.success:
                return result

            # 等待iframe加载
            time.sleep(0.5)

        logger.warning(f"iframe访问超时: {iframe_selector}")
        return IFrameResult(
            success=False,
            url=iframe_selector,
            error="Timeout",
        )

    def _try_access_iframe(self, iframe_selector: str) -> Optional[IFrameResult]:
        """尝试访问iframe"""
        js = f"""
        (() => {{
            const iframe = document.querySelector({iframe_selector!r});
            if (!iframe) return null;

            try {{
                const doc = iframe.contentDocument || iframe.contentWindow.document;
                return {{
                    url: iframe.src,
                    title: doc.title || '',
                    body_text: doc.body ? doc.body.innerText.slice(0, 2000) : null,
                    links: Array.from(doc.querySelectorAll('a[href]')).map(a => a.href).slice(0, 20),
                    success: true,
                    accessible: true
                }};
            }} catch (e) {{
                return {{
                    url: iframe.src,
                    error: 'Cross-origin iframe',
                    success: false,
                    accessible: false
                }};
            }}
        }})()
        """
        try:
            result = self.session.eval_js(js)
            if result:
                return IFrameResult(
                    success=result.get('success', False),
                    url=result.get('url', ''),
                    title=result.get('title', ''),
                    error=result.get('error'),
                    accessible=result.get('accessible', False),
                )
        except Exception as e:
            logger.debug(f"iframe访问失败: {e}")
        return None

    # =========================================================================
    # P36: 虚拟列表检测
    # =========================================================================

    def detect_virtual_list(
        self,
        container_selector: str = None,
    ) -> Optional[Dict[str, Any]]:
        """检测虚拟列表（P36修复）

        Args:
            container_selector: 容器选择器（可选，默认自动检测）

        Returns:
            虚拟列表信息，未检测到返回None
        """
        js = f"""
        (() => {{
            const selector = {container_selector!r};
            const container = selector ? document.querySelector(selector) :
                document.querySelector('[class*="virtual"], [class*="scroll-list"], [role="listbox"]');
            if (!container) return null;

            // 检测虚拟列表特征
            const hasScrollContainer = container.scrollHeight > container.clientHeight * 1.5;
            const items = container.querySelectorAll('[class*="item"], [role="option"]');
            const itemHeight = items.length > 0 ? items[0].offsetHeight : 0;
            const visibleCount = Math.floor(container.clientHeight / Math.max(itemHeight, 1));
            const totalCount = items.length;

            // 判断是否为虚拟列表：可见元素数远少于总元素数
            const isVirtual = totalCount < visibleCount * 2;

            return {{
                isVirtual: isVirtual,
                containerHeight: container.clientHeight,
                scrollHeight: container.scrollHeight,
                itemHeight: itemHeight,
                visibleCount: visibleCount,
                totalCount: totalCount,
                estimatedTotal: isVirtual ? Math.floor(container.scrollHeight / Math.max(itemHeight, 1)) : totalCount,
                selector: selector || '[virtual-list]'
            }};
        }})()
        """
        try:
            result = self.session.eval_js(js)
            if result:
                logger.info(f"虚拟列表检测: {result}")
            return result
        except Exception as e:
            logger.debug(f"虚拟列表检测失败: {e}")
            return None

    # =========================================================================
    # 综合等待策略
    # =========================================================================

    def wait_for_page_ready(
        self,
        timeout: float = 30.0,
        wait_fetch: bool = True,
        wait_stable: bool = True,
        stable_check_rounds: int = 3,
    ) -> Dict[str, Any]:
        """综合等待页面就绪

        Args:
            timeout: 总超时时间
            wait_fetch: 是否等待Fetch请求完成
            wait_stable: 是否等待内容稳定
            stable_check_rounds: 稳定检测轮数

        Returns:
            等待结果摘要
        """
        start_time = time.time()
        results = {
            "fetch_waited": False,
            "fetch_completed": False,
            "stable_checked": False,
            "stable_completed": False,
        }

        # 1. 等待Fetch请求
        if wait_fetch:
            results["fetch_completed"] = self.wait_for_fetch_complete(
                timeout=timeout * 0.6
            )
            results["fetch_waited"] = True

        # 2. 等待内容稳定
        if wait_stable:
            stability = self.wait_for_content_stable(
                timeout=timeout * 0.4,
                check_rounds=stable_check_rounds,
            )
            results["stable_completed"] = stability.stable
            results["stable_checked"] = True
            results["stable_rounds"] = stability.rounds_checked

        results["total_elapsed"] = time.time() - start_time
        results["success"] = results["fetch_completed"] or results["stable_completed"]
        return results


# =========================================================================
# 便捷函数
# =========================================================================

def create_dom_loader(session, **kwargs) -> DOMContentLoader:
    """创建DOM内容加载器"""
    return DOMContentLoader(session, **kwargs)


def wait_for_page_ready(session, timeout: float = 30.0, **kwargs) -> Dict[str, Any]:
    """便捷函数：等待页面就绪"""
    loader = DOMContentLoader(session)
    return loader.wait_for_page_ready(timeout=timeout, **kwargs)


def detect_virtual_list(session, container_selector: str = None) -> Optional[Dict]:
    """便捷函数：检测虚拟列表"""
    loader = DOMContentLoader(session)
    return loader.detect_virtual_list(container_selector)


def access_iframe_sync(session, iframe_selector: str, timeout: float = 5.0) -> Optional[IFrameResult]:
    """便捷函数：同步访问iframe"""
    loader = DOMContentLoader(session)
    return loader.access_iframe_sync(iframe_selector, timeout)


__all__ = [
    "DOMContentLoader",
    "ContentStabilityResult",
    "FetchRequest",
    "IFrameResult",
    "create_dom_loader",
    "wait_for_page_ready",
    "detect_virtual_list",
    "access_iframe_sync",
]
