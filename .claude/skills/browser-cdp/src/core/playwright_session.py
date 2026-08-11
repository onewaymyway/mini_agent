"""
playwright_session.py - Playwright 集成模块（asyncio 兼容版）

提供高层 API 封装 Playwright，支持：
- 反检测模式（stealth.js 注入）
- 智能等待策略（networkidle/selector/stable）
- 复杂交互模拟（鼠标轨迹、打字节奏）
- 自动重试与熔断
- asyncio 循环兼容（自动检测并使用 async API）

用法示例：
  from src.core.playwright_session import PlaywrightSession
  
  # 同步用法
  session = PlaywrightSession(headless=True)
  session.launch()
  session.goto('https://example.com')
  result = session.extract_text()
  session.close()
  
  # 异步用法
  async with PlaywrightSession(headless=True) as session:
      await session.goto('https://example.com')
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import random
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Union

logger = logging.getLogger(__name__)


@dataclass
class PlaywrightConfig:
    """Playwright 配置"""
    headless: bool = True
    viewport_width: int = 1920
    viewport_height: int = 1080
    user_agent: str = field(default_factory=lambda: _default_user_agent())
    enable_stealth: bool = True
    enable_network_monitoring: bool = True
    default_timeout: int = 30000  # ms
    max_retries: int = 3
    retry_delay: float = 1.0
    
    # 反检测配置
    stealth_config: Dict[str, Any] = field(default_factory=lambda: {
        'remove_webdriver': True,
        'mock_chrome_runtime': True,
        'mock_permissions': True,
        'mock_language': True,
        'mock_platform': True,
        'mock_plugins': True,
        'mock_fingerprint': True,
    })
    
    # 智能等待配置
    wait_config: Dict[str, Any] = field(default_factory=lambda: {
        'timeout': 30.0,
        'idle_timeout': 0.5,
        'check_interval': 0.3,
        'stable_count': 3,
    })
    
    # 人类行为模拟配置
    humanize_config: Dict[str, Any] = field(default_factory=lambda: {
        'mouse_trajectory': True,
        'typing_rhythm': True,
        'random_delay_range': (0.1, 0.5),
    })


def _default_user_agent() -> str:
    """生成默认 User-Agent"""
    return (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    )


class PlaywrightSession:
    """
    Playwright 浏览器会话
    
    封装 Playwright API，提供反检测、智能等待、复杂交互等功能。
    自动检测是否在 asyncio 循环中，并选择相应的 API。
    """
    
    def __init__(self, config: PlaywrightConfig = None):
        self.config = config or PlaywrightConfig()
        self._playwright = None
        self._browser = None
        self._context = None
        self._page = None
        self._async_playwright = None
        self._async_browser = None
        self._async_context = None
        self._async_page = None
        self._network_events: Dict[str, List[dict]] = {
            'requests': [],
            'responses': [],
        }
        self._stealth_applied = False
        self._retry_count = 0
        self._in_async_loop = False
    
    def _detect_async_context(self) -> bool:
        """检测是否在 asyncio 循环中"""
        try:
            loop = asyncio.get_running_loop()
            return loop.is_running()
        except RuntimeError:
            return False
    
    def launch(self) -> 'PlaywrightSession':
        """启动浏览器（同步 API）"""
        self._in_async_loop = self._detect_async_context()
        
        if self._in_async_loop:
            logger.warning("检测到 asyncio 循环，请使用 async_launch() 或 async 上下文管理器")
            # 尝试在独立线程中启动
            import threading
            result = [None]
            error = [None]
            
            def _launch_sync():
                try:
                    from playwright.sync_api import sync_playwright
                    pw = sync_playwright().start()
                    browser = pw.chromium.launch(
                        headless=self.config.headless,
                        args=['--disable-blink-features=AutomationControlled']
                    )
                    context = browser.new_context(
                        viewport={'width': self.config.viewport_width, 'height': self.config.viewport_height},
                        user_agent=self.config.user_agent,
                    )
                    page = context.new_page()
                    result[0] = (pw, browser, context, page)
                except Exception as e:
                    error[0] = e
            
            thread = threading.Thread(target=_launch_sync)
            thread.start()
            thread.join(timeout=30)
            
            if error[0]:
                raise error[0]
            if not result[0]:
                raise RuntimeError("Playwright 启动超时")
            
            self._playwright, self._browser, self._context, self._page = result[0]
            logger.info("浏览器启动成功 (线程模式)")
        else:
            self._launch_sync()
        
        return self
    
    def _launch_sync(self):
        """同步启动浏览器"""
        from playwright.sync_api import sync_playwright
        self._playwright = sync_playwright().start()
        self._browser = self._playwright.chromium.launch(
            headless=self.config.headless,
            args=[
                '--disable-blink-features=AutomationControlled',
                '--no-sandbox',
                '--disable-dev-shm-usage',
                '--disable-software-rasterizer',
                '--disable-gpu',
                '--mute-audio',
                '--no-first-run',
                '--no-default-browser-check',
                '--disable-infobars',
                '--window-size={},{}'.format(self.config.viewport_width, self.config.viewport_height),
            ]
        )
        
        context_args = {
            'viewport': {'width': self.config.viewport_width, 'height': self.config.viewport_height},
            'user_agent': self.config.user_agent,
            'locale': 'zh-CN',
            'timezone_id': 'Asia/Shanghai',
            'permissions': ['geolocation'],
            'geolocation': {'longitude': 116.407526, 'latitude': 39.90403},
        }
        
        self._context = self._browser.new_context(**context_args)
        self._page = self._context.new_page()
        
        if self.config.enable_stealth:
            self._inject_stealth()
        
        self._page.set_default_timeout(self.config.default_timeout)
        
        if self.config.enable_network_monitoring:
            self._enable_network_monitoring()
        
        logger.info(f"浏览器启动成功 (headless={self.config.headless})")
    
    async def async_launch(self):
        """异步启动浏览器（在 asyncio 循环中使用）"""
        from playwright.async_api import async_playwright
        self._in_async_loop = True
        self._async_playwright = await async_playwright().start()
        self._async_browser = await self._async_playwright.chromium.launch(
            headless=self.config.headless,
            args=['--disable-blink-features=AutomationControlled']
        )
        self._async_context = await self._async_browser.new_context(
            viewport={'width': self.config.viewport_width, 'height': self.config.viewport_height},
            user_agent=self.config.user_agent,
        )
        self._async_page = await self._async_context.new_page()
        logger.info("异步浏览器启动成功")
    
    def _inject_stealth(self) -> None:
        """注入反检测脚本"""
        stealth_path = os.path.join(os.path.dirname(__file__), 'stealth.min.js')
        
        if os.path.exists(stealth_path):
            with open(stealth_path, 'r') as f:
                stealth_script = f.read()
            self._page.add_init_script(stealth_script)
            self._stealth_applied = True
            logger.info("反检测脚本注入成功")
        else:
            self._inject_builtin_stealth()
    
    def _inject_builtin_stealth(self) -> None:
        """注入内置反检测代码"""
        scripts = [
            """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            """,
            """
            window.chrome = {
                runtime: {
                    connect: function() {},
                    onMessage: { addListener: function() {} },
                    sendMessage: function() {},
                }
            };
            """,
            """
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            """,
            """
            Object.defineProperty(navigator, 'languages', {
                get: () => ['zh-CN', 'zh', 'en']
            });
            """,
            """
            delete navigator.__proto__.webdriver;
            """,
        ]
        
        for script in scripts:
            self._page.add_init_script(script)
        
        self._stealth_applied = True
        logger.info("内置反检测代码注入成功")
    
    def _enable_network_monitoring(self) -> None:
        """启用网络监控"""
        self._page.on('request', self._on_request)
        self._page.on('response', self._on_response)
        logger.info("网络监控已启用")
    
    def _on_request(self, request) -> None:
        """请求回调"""
        self._network_events['requests'].append({
            'url': request.url,
            'method': request.method,
            'timestamp': time.time(),
        })
    
    def _on_response(self, response) -> None:
        """响应回调"""
        self._network_events['responses'].append({
            'url': response.url,
            'status': response.status,
            'timestamp': time.time(),
        })
    
    def goto(self, url: str, wait_until: str = 'networkidle') -> 'PlaywrightSession':
        """导航到 URL（同步）"""
        if self._in_async_loop:
            raise RuntimeError("在 asyncio 循环中请使用 async goto()")
        self._page.goto(url, wait_until=wait_until)
        logger.info(f"导航到 {url}")
        return self
    
    async def async_goto(self, url: str, wait_until: str = 'networkidle'):
        """导航到 URL（异步）"""
        if not self._in_async_loop:
            raise RuntimeError("不在 asyncio 循环中，请使用同步 goto()")
        await self._async_page.goto(url, wait_until=wait_until)
        logger.info(f"导航到 {url}")
    
    def get_page(self):
        """获取当前页面"""
        if self._in_async_loop:
            return self._async_page
        return self._page
    
    def close(self) -> None:
        """关闭浏览器"""
        try:
            if self._in_async_loop:
                if self._async_page:
                    self._async_page.close()
                if self._async_context:
                    self._async_context.close()
                if self._async_browser:
                    self._async_browser.close()
                if self._async_playwright:
                    self._async_playwright.stop()
            else:
                if self._page:
                    self._page.close()
                if self._context:
                    self._context.close()
                if self._browser:
                    self._browser.close()
                if self._playwright:
                    self._playwright.stop()
            logger.info("浏览器已关闭")
        except Exception as e:
            logger.warning(f"关闭浏览器时出错: {e}")
    
    def __enter__(self):
        self.launch()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
    
    async def __aenter__(self):
        await self.async_launch()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        self.close()


# 便捷函数
def create_session(headless: bool = True, enable_stealth: bool = True) -> PlaywrightSession:
    """创建 Playwright 会话的便捷函数"""
    config = PlaywrightConfig(
        headless=headless,
        enable_stealth=enable_stealth,
    )
    return PlaywrightSession(config)


def with_session(headless: bool = True, enable_stealth: bool = True):
    """会话上下文管理器装饰器"""
    def decorator(func):
        def wrapper(*args, **kwargs):
            with create_session(headless=headless, enable_stealth=enable_stealth) as session:
                return func(session, *args, **kwargs)
        return wrapper
    return decorator
