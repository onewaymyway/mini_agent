"""
network_idle_detector.py - 网络空闲检测器

提供稳健的网络空闲检测，支持关键请求过滤和多轮确认。

与 smart_wait.py 的 _wait_network_idle 相比，本模块更独立、更聚焦：
- 独立的 CDP Network 事件管理
- 多层确认机制（关键请求为空 + 持续 idle 窗口）
- 请求统计与可观测性
- 支持自定义过滤规则
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable

logger = logging.getLogger(__name__)


# 静态资源扩展名（非关键请求）
STATIC_EXTENSIONS = {
    '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico', '.bmp',
    '.css', '.js', '.mjs',
    '.woff', '.woff2', '.ttf', '.eot', '.otf', '.svg',
    '.mp4', '.mp3', '.webm', '.avi', '.mov', '.flv',
    '.pdf', '.zip', '.tar', '.gz',
    '.map',  # sourcemap
}

# 静态资源 MIME 类型前缀（非关键）
STATIC_MIME_PREFIXES = (
    'image/', 'text/css', 'application/javascript', 'application/x-javascript',
    'font/', 'video/', 'audio/',
    'application/x-shockwave-flash',
)


@dataclass
class NetworkStats:
    """网络请求统计"""
    total_requests: int = 0
    critical_requests: int = 0
    static_requests: int = 0
    xhr_fetch_requests: int = 0
    final_pending: int = 0
    final_critical_pending: int = 0
    matched_urls: List[str] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        return {
            'total_requests': self.total_requests,
            'critical_requests': self.critical_requests,
            'static_requests': self.static_requests,
            'xhr_fetch_requests': self.xhr_fetch_requests,
            'final_pending': self.final_pending,
            'final_critical_pending': self.final_critical_pending,
            'matched_urls': self.matched_urls[-20:],  # 只保留最近20条
        }


@dataclass
class NetworkIdleConfig:
    """网络空闲检测配置"""
    # 空闲确认时长（秒）：关键请求为 0 后持续多久才算空闲
    idle_window: float = 0.5
    # 总超时（秒）
    timeout: float = 30.0
    # 是否只等待关键请求（忽略静态资源）
    wait_critical_only: bool = True
    # 检查间隔（秒）
    check_interval: float = 0.2
    # URL 过滤模式（正则列表，匹配则视为关键请求）
    url_patterns: List[str] = field(default_factory=list)
    # 排除的 URL 模式（正则列表）
    excluded_patterns: List[str] = field(default_factory=list)
    # 最大跟踪请求数（防止内存泄漏）
    max_tracked: int = 500


class NetworkIdleDetector:
    """
    网络空闲检测器

    通过 CDP Network domain 监听请求事件，统计关键请求，
    当关键请求全部完成并持续 idle_window 秒无新关键请求时判定网络空闲。
    """

    def __init__(self, session, config: Optional[NetworkIdleConfig] = None):
        self.session = session
        self.config = config or NetworkIdleConfig()
        self._stats = NetworkStats()
        self._request_tracker: Dict[str, dict] = {}
        self._pending_requests = 0
        self._critical_pending = 0
        self._idle_since = 0.0
        self._network_enabled = False
        self._callbacks: List[Callable] = []
        self._idle_event = asyncio.Event()  # 用于异步等待

    # =========================================================================
    # CDP 事件处理
    # =========================================================================

    def _on_request_will_be_sent(self, params: dict) -> None:
        """CDP Network.requestWillBeSent 回调"""
        request_id = params.get('requestId', '')
        self._pending_requests += 1
        self._stats.total_requests += 1

        request = params.get('request', {})
        url = request.get('url', '')
        initiator = request.get('initiator', {})

        # 记录请求
        if len(self._request_tracker) < self.config.max_tracked:
            self._request_tracker[request_id] = {
                'url': url,
                'method': request.get('method', 'GET'),
                'timestamp': time.time(),
                'initiator_type': initiator.get('type', ''),
            }

        # 判断是否为关键请求
        if self._is_critical_request(params):
            self._critical_pending += 1
            self._stats.critical_requests += 1

        # 统计 XHR/Fetch
        if initiator.get('type') in ('xhr', 'fetch'):
            self._stats.xhr_fetch_requests += 1

        # 触发回调
        for cb in self._callbacks:
            try:
                cb('request', params)
            except Exception as e:
                logger.debug(f"NetworkIdleDetector: 回调出错: {e}")

    def _on_loading_finished(self, params: dict) -> None:
        """CDP Network.loadingFinished 回调"""
        request_id = params.get('requestId', '')
        self._pending_requests = max(0, self._pending_requests - 1)

        if request_id in self._request_tracker:
            req = self._request_tracker.pop(request_id)
            if self._is_critical_request({'request': req}):
                self._critical_pending = max(0, self._critical_pending - 1)

        # 触发回调
        for cb in self._callbacks:
            try:
                cb('finish', params)
            except Exception as e:
                logger.debug(f"NetworkIdleDetector: 回调出错: {e}")

    def _on_response_received(self, params: dict) -> None:
        """CDP Network.responseReceived 回调"""
        request_id = params.get('requestId', '')
        response = params.get('response', {})
        url = response.get('url', '')

        # 更新统计
        if request_id in self._request_tracker:
            self._request_tracker[request_id]['status'] = response.get('status', 0)
            self._request_tracker[request_id]['mimeType'] = response.get('mimeType', '')

        # 如果 URL 匹配跟踪模式，记录
        for pattern in self.config.url_patterns:
            if re.search(pattern, url, re.IGNORECASE):
                self._stats.matched_urls.append(url)
                break

        for cb in self._callbacks:
            try:
                cb('response', params)
            except Exception as e:
                logger.debug(f"NetworkIdleDetector: 回调出错: {e}")

    # =========================================================================
    # 请求分类
    # =========================================================================

    def _is_critical_request(self, params: dict) -> bool:
        """判断请求是否关键"""
        request = params.get('request', {})
        url = request.get('url', '')
        initiator = request.get('initiator', {})

        # 检查排除模式
        for pattern in self.config.excluded_patterns:
            if re.search(pattern, url, re.IGNORECASE):
                return False

        # XHR/Fetch 总是关键请求
        if initiator.get('type') in ('xhr', 'fetch'):
            return True

        # 检查 URL 模式
        for pattern in self.config.url_patterns:
            if re.search(pattern, url, re.IGNORECASE):
                return True

        # 检查 MIME 类型
        # （在 loadingFinished 中更准确，这里用扩展名兜底）
        url_lower = url.lower().split('?')[0]  # 去掉 query string
        for ext in STATIC_EXTENSIONS:
            if url_lower.endswith(ext):
                return False

        # 默认：有 URL 路径且不含常见静态扩展名的视为关键请求
        return '/' in url and not any(url_lower.endswith(p) for p in STATIC_EXTENSIONS)

    # =========================================================================
    # 生命周期管理
    # =========================================================================

    def start_listening(self) -> None:
        """启动网络监听"""
        if self._network_enabled:
            return
        self.session.subscribe('Network.requestWillBeSent', self._on_request_will_be_sent)
        self.session.subscribe('Network.loadingFinished', self._on_loading_finished)
        self.session.subscribe('Network.responseReceived', self._on_response_received)
        try:
            self.session.send('Network.enable')
            self._network_enabled = True
            logger.debug("NetworkIdleDetector: 已启动网络监听")
        except Exception as e:
            logger.warning(f"NetworkIdleDetector: 启用 Network domain 失败: {e}")

    def stop_listening(self) -> None:
        """停止网络监听"""
        if not self._network_enabled:
            return
        try:
            self.session.unsubscribe('Network.requestWillBeSent', self._on_request_will_be_sent)
            self.session.unsubscribe('Network.loadingFinished', self._on_loading_finished)
            self.session.unsubscribe('Network.responseReceived', self._on_response_received)
            self.session.send('Network.disable')
        except Exception as e:
            logger.debug(f"NetworkIdleDetector: 关闭 Network domain 失败（可忽略）: {e}")
        finally:
            self._network_enabled = False
            self._pending_requests = 0
            self._critical_pending = 0
            self._idle_since = 0.0
            self._request_tracker.clear()
            logger.debug("NetworkIdleDetector: 已停止网络监听")

    def add_callback(self, callback: Callable) -> None:
        """添加事件回调"""
        self._callbacks.append(callback)

    def remove_callback(self, callback: Callable) -> None:
        """移除事件回调"""
        if callback in self._callbacks:
            self._callbacks.remove(callback)

    def get_stats(self) -> NetworkStats:
        """获取当前网络统计"""
        self._stats.final_pending = self._pending_requests
        self._stats.final_critical_pending = self._critical_pending
        return self._stats

    def reset_stats(self) -> None:
        """重置统计"""
        self._stats = NetworkStats()

    # =========================================================================
    # 核心方法：等待网络空闲
    # =========================================================================

    async def wait_for_idle(
        self,
        idle_window: float = None,
        timeout: float = None,
        wait_critical_only: bool = None,
    ) -> dict:
        """
        等待网络空闲

        实现逻辑：
        1. 启动 CDP Network 事件监听
        2. 每 check_interval 秒检查 critical_pending 计数
        3. 当 critical_pending == 0 时开始计时
        4. 持续 idle_window 秒无新关键请求则返回成功

        Args:
            idle_window: 空闲确认时长（秒），覆盖配置
            timeout: 总超时时间（秒），覆盖配置
            wait_critical_only: 是否只等待关键请求，覆盖配置

        Returns:
            dict: {success, elapsed, stats, idle_window_used}
        """
        idle_window = idle_window or self.config.idle_window
        timeout = timeout or self.config.timeout
        wait_critical_only = wait_critical_only if wait_critical_only is not None else self.config.wait_critical_only

        self.start_listening()
        self.reset_stats()
        self._idle_since = 0.0

        deadline = time.time() + timeout
        last_log_time = time.time()

        logger.info(f"开始等待网络空闲: idle_window={idle_window}s, timeout={timeout}s")

        while time.time() < deadline:
            # 根据 wait_critical_only 选择计数方式
            current_pending = self._critical_pending if wait_critical_only else self._pending_requests

            if current_pending == 0:
                if self._idle_since == 0:
                    self._idle_since = time.time()
                # 检查是否持续了足够的空闲时间
                if time.time() - self._idle_since >= idle_window:
                    elapsed = time.time() - deadline + timeout
                    self._stats.final_pending = self._pending_requests
                    self._stats.final_critical_pending = self._critical_pending
                    logger.info(f"网络空闲检测通过: 耗时 {elapsed:.2f}s, 关键请求={self._critical_pending}, 总请求={self._pending_requests}")
                    self.stop_listening()
                    return {
                        'success': True,
                        'elapsed': elapsed,
                        'stats': self._stats.to_dict(),
                        'idle_window_used': idle_window,
                    }
            else:
                # 有新请求，重置空闲计时
                self._idle_since = 0.0

            # 定期日志
            now = time.time()
            if now - last_log_time >= 2.0:
                logger.debug(f"网络状态: 关键请求={self._critical_pending}, 总请求={self._pending_requests}, 空闲计时={time.time()-self._idle_since:.1f}s/{idle_window}s")
                last_log_time = now

            await asyncio.sleep(self.config.check_interval)

        # 超时
        elapsed = time.time() - deadline + timeout
        self._stats.final_pending = self._pending_requests
        self._stats.final_critical_pending = self._critical_pending
        logger.warning(f"网络空闲检测超时: 耗时 {elapsed:.2f}s, 最终关键请求={self._critical_pending}")
        self.stop_listening()
        return {
            'success': False,
            'elapsed': elapsed,
            'stats': self._stats.to_dict(),
            'idle_window_used': idle_window,
            'error': 'timeout',
        }

    async def wait_for_idle_async(
        self,
        idle_window: float = None,
        timeout: float = None,
        wait_critical_only: bool = None,
    ) -> bool:
        """
        异步等待网络空闲（使用 Event 通知）

        适合在后台任务中等待，不阻塞主循环。

        Returns:
            bool: 是否成功等待到网络空闲
        """
        result = await self.wait_for_idle(
            idle_window=idle_window,
            timeout=timeout,
            wait_critical_only=wait_critical_only,
        )
        return result.get('success', False)

    # =========================================================================
    # 便捷方法
    # =========================================================================

    async def wait_for_xhr_idle(
        self,
        timeout: float = None,
    ) -> dict:
        """
        等待所有 XHR/Fetch 请求完成

        这是最常用的场景：页面数据加载完成后等待网络空闲。

        Args:
            timeout: 超时时间（秒）

        Returns:
            dict: 等待结果
        """
        return await self.wait_for_idle(
            timeout=timeout,
            wait_critical_only=True,
        )

    async def wait_for_full_idle(
        self,
        idle_window: float = None,
        timeout: float = None,
    ) -> dict:
        """
        等待所有请求（包括静态资源）完成

        Args:
            idle_window: 空闲确认时长
            timeout: 总超时

        Returns:
            dict: 等待结果
        """
        return await self.wait_for_idle(
            idle_window=idle_window,
            timeout=timeout,
            wait_critical_only=False,
        )

    def get_current_request_count(self) -> dict:
        """获取当前活跃请求数"""
        return {
            'pending': self._pending_requests,
            'critical_pending': self._critical_pending,
            'tracked': len(self._request_tracker),
        }


# =========================================================================
# 模块级便捷函数
# =========================================================================

async def network_idle(
    session,
    idle_seconds: float = 0.5,
    timeout: float = 30.0,
    critical_only: bool = True,
) -> dict:
    """
    快捷函数：等待网络空闲

    Args:
        session: CDP session 对象（需支持 subscribe/send/eval_js）
        idle_seconds: 空闲确认时长
        timeout: 总超时
        critical_only: 是否只等待关键请求

    Returns:
        dict: {success, elapsed, stats}
    """
    detector = NetworkIdleDetector(session)
    detector.config.idle_window = idle_seconds
    detector.config.timeout = timeout
    detector.config.wait_critical_only = critical_only
    return await detector.wait_for_idle()


def create_network_idle_detector(session, **kwargs) -> NetworkIdleDetector:
    """
    工厂函数：创建网络空闲检测器

    Args:
        session: CDP session 对象
        **kwargs: 传递给 NetworkIdleConfig 的参数

    Returns:
        NetworkIdleDetector 实例
    """
    config = NetworkIdleConfig(**kwargs)
    return NetworkIdleDetector(session, config)
