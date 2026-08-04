"""
playwright_session.py - Playwright 集成模块

提供高层 API 封装 Playwright，支持：
- 反检测模式（stealth.js 注入）
- 智能等待策略（networkidle/selector/stable）
- 复杂交互模拟（鼠标轨迹、打字节奏）
- 自动重试与熔断

用法示例：
  from src.core.playwright_session import PlaywrightSession
  
  session = PlaywrightSession(headless=True)
  session.launch()
  session.goto('https://example.com')
  session.wait_for('networkidle')
  result = session.extract_text()
  session.close()
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

from playwright.sync_api import Page, Playwright, sync_playwright

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
    default_timeout: float = 30000  # ms
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
    """
    
    def __init__(self, config: PlaywrightConfig = None):
        self.config = config or PlaywrightConfig()
        self._playwright: Optional[Playwright] = None
        self._browser = None
        self._page: Optional[Page] = None
        self._network_events: Dict[str, List[dict]] = {
            'requests': [],
            'responses': [],
        }
        self._stealth_applied = False
        self._retry_count = 0
    
    def launch(self) -> 'PlaywrightSession':
        """启动浏览器"""
        self._playwright = sync_playwright().start()
        
        # 启动 Chromium
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
        
        # 创建上下文（支持反检测）
        context_args = {
            'viewport': {'width': self.config.viewport_width, 'height': self.config.viewport_height},
            'user_agent': self.config.user_agent,
            'locale': 'zh-CN',
            'timezone_id': 'Asia/Shanghai',
            'permissions': ['geolocation'],
            'geolocation': {'longitude': 116.407526, 'latitude': 39.90403},
        }
        
        self._context = self._browser.new_context(**context_args)
        
        # 创建页面
        self._page = self._context.new_page()
        
        # 注入反检测脚本
        if self.config.enable_stealth:
            self._inject_stealth()
        
        # 设置超时
        self._page.set_default_timeout(self.config.default_timeout)
        
        # 启用网络监控
        if self.config.enable_network_monitoring:
            self._enable_network_monitoring()
        
        logger.info(f"浏览器启动成功 (headless={self.config.headless})")
        return self
    
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
            # 使用内置的反检测代码
            self._inject_builtin_stealth()
    
    def _inject_builtin_stealth(self) -> None:
        """注入内置反检测代码"""
        scripts = [
            # 移除 navigator.webdriver
            """
            Object.defineProperty(navigator, 'webdriver', {
                get: () => undefined
            });
            """,
            # 模拟 Chrome runtime
            """
            window.chrome = {
                runtime: {
                    connect: function() {},
                    onMessage: { addListener: function() {} },
                    sendMessage: function() {},
                }
            };
            """,
            # 模拟 plugins
            """
            Object.defineProperty(navigator, 'plugins', {
                get: () => [1, 2, 3, 4, 5]
            });
            """,
            # 模拟 language
            """
            Object.defineProperty(navigator, 'languages', {
                get: () => ['zh-CN', 'zh', 'en']
            });
            """,
            # 移除 Automation 特征
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
        """
        导航到 URL
        
        Args:
            url: 目标 URL
            wait_until: 等待策略 (load/domcontentloaded/networkidle/commit)
        """
        self._page.goto(url, wait_until=wait_until)
        logger.info(f"导航到 {url}")
        return self
    
    def wait_for(self, strategy: str, **kwargs) -> bool:
        """
        智能等待
        
        Args:
            strategy: 等待策略 (networkidle/selector/stable)
            **kwargs: 策略参数
        """
        strategies = {
            'networkidle': self._wait_network_idle,
            'selector': self._wait_selector,
            'stable': self._wait_stable,
        }
        
        if strategy not in strategies:
            raise ValueError(f"未知的等待策略: {strategy}")
        
        logger.info(f"开始等待策略: {strategy}")
        start_time = time.time()
        
        try:
            result = strategies[strategy](**kwargs)
            elapsed = time.time() - start_time
            logger.info(f"等待策略 {strategy} 完成，耗时 {elapsed:.2f}s")
            return result
        except Exception as e:
            elapsed = time.time() - start_time
            logger.warning(f"等待策略 {strategy} 失败，耗时 {elapsed:.2f}s: {e}")
            return False
    
    def _wait_network_idle(self, idle_timeout: float = 0.5) -> bool:
        """等待网络空闲"""
        deadline = time.time() + self.config.wait_config['timeout']
        
        while time.time() < deadline:
            # 检查最近 idle_timeout 内是否有新请求
            recent_requests = [
                r for r in self._network_events['requests']
                if time.time() - r['timestamp'] < idle_timeout
            ]
            
            if len(recent_requests) == 0:
                return True
            
            time.sleep(self.config.wait_config['check_interval'])
        
        return False
    
    def _wait_selector(self, selector: str, timeout: float = 30.0) -> bool:
        """等待选择器出现"""
        try:
            self._page.wait_for_selector(selector, timeout=timeout * 1000)
            return True
        except Exception:
            return False
    
    def _wait_stable(self, selector: str, stable_count: int = 3) -> bool:
        """等待内容稳定"""
        previous_content = None
        stable_count = 0
        
        deadline = time.time() + self.config.wait_config['timeout']
        
        while time.time() < deadline:
            try:
                element = self._page.query_selector(selector)
                if element:
                    current_content = element.inner_text()
                    if current_content == previous_content:
                        stable_count += 1
                        if stable_count >= stable_count:
                            return True
                    else:
                        stable_count = 0
                    previous_content = current_content
            except Exception:
                pass
            
            time.sleep(self.config.wait_config['check_interval'])
        
        return False
    
    def click(self, selector: str, delay: float = 0.1) -> 'PlaywrightSession':
        """
        点击元素（带人类行为模拟）
        
        Args:
            selector: CSS 选择器
            delay: 点击后延迟
        """
        self._humanize_click(selector)
        time.sleep(delay)
        return self
    
    def _humanize_click(self, selector: str) -> None:
        """模拟人类点击行为"""
        if self.config.humanize_config['mouse_trajectory']:
            # 获取元素位置
            element = self._page.query_selector(selector)
            if element:
                box = element.bounding_box()
                if box:
                    # 模拟鼠标移动到元素
                    self._page.mouse.move(box['x'] + box['width'] / 2, box['y'] + box['height'] / 2)
                    time.sleep(random.uniform(0.1, 0.3))
        
        # 执行点击
        self._page.click(selector)
    
    def type_text(self, selector: str, text: str, delay: float = 0.05) -> 'PlaywrightSession':
        """
        输入文本（带人类行为模拟）
        
        Args:
            selector: CSS 选择器
            text: 要输入的文本
            delay: 每个字符的延迟
        """
        self._page.click(selector)
        self._page.fill(selector, text)
        
        if self.config.humanize_config['typing_rhythm']:
            # 模拟打字节奏
            for char in text:
                time.sleep(random.uniform(delay * 0.5, delay * 1.5))
        
        return self
    
    def scroll(self, direction: str = 'down', amount: int = 500) -> 'PlaywrightSession':
        """
        滚动页面
        
        Args:
            direction: 滚动方向 (up/down)
            amount: 滚动量
        """
        if direction == 'down':
            self._page.evaluate(f'window.scrollBy(0, {amount})')
        else:
            self._page.evaluate(f'window.scrollBy(0, -{amount})')
        
        time.sleep(random.uniform(0.1, 0.3))
        return self
    
    def extract_text(self, selector: str = None) -> str:
        """提取页面文本"""
        if selector:
            element = self._page.query_selector(selector)
            if element:
                return element.inner_text()
        return self._page.content()
    
    def extract_links(self) -> List[Dict[str, str]]:
        """提取页面链接"""
        links = self._page.evaluate('''
            () => {
                return Array.from(document.querySelectorAll('a[href]'))
                    .map(a => ({
                        text: a.textContent.trim(),
                        href: a.href
                    }))
                    .filter(l => l.text && l.href);
            }
        ''')
        return links
    
    def screenshot(self, path: str = None, full_page: bool = True) -> str:
        """
        截图
        
        Args:
            path: 保存路径
            full_page: 是否全页截图
        """
        if path is None:
            path = f"temp_data/screenshot_{int(time.time())}.png"
        
        os.makedirs(os.path.dirname(path), exist_ok=True)
        self._page.screenshot(path=path, full_page=full_page)
        logger.info(f"截图已保存: {path}")
        return path
    
    def evaluate(self, script: str, **kwargs) -> Any:
        """执行 JavaScript"""
        return self._page.evaluate(script, **kwargs)
    
    def get_network_events(self) -> Dict[str, List[dict]]:
        """获取网络事件"""
        return self._network_events.copy()
    
    def close(self) -> None:
        """关闭浏览器"""
        if self._page:
            self._page.close()
        if self._context:
            self._context.close()
        if self._browser:
            self._browser.close()
        if self._playwright:
            self._playwright.stop()
        logger.info("浏览器已关闭")
    
    def __enter__(self):
        self.launch()
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()


class PlaywrightSessionAsync:
    """异步版本的 PlaywrightSession"""
    
    def __init__(self, config: PlaywrightConfig = None):
        self.config = config or PlaywrightConfig()
        self._playwright = None
        self._browser = None
        self._page = None
    
    async def launch(self):
        """异步启动浏览器"""
        self._playwright = await sync_playwright().start()
        self._browser = await self._playwright.chromium.launch(
            headless=self.config.headless,
            args=['--disable-blink-features=AutomationControlled']
        )
        self._page = await self._browser.new_page()
        return self
    
    async def close(self):
        """异步关闭浏览器"""
        if self._page:
            await self._page.close()
        if self._browser:
            await self._browser.close()
        if self._playwright:
            self._playwright.stop()


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
