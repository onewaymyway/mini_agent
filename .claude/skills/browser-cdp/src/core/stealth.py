"""
stealth.py - 反检测模块

隐藏自动化特征，模拟真实浏览器行为。

核心功能：
- 移除 navigator.webdriver 属性
- 模拟真实浏览器指纹
- 人类行为模拟（鼠标轨迹、打字节奏）
- 请求间隔随机化
"""
from __future__ import annotations

import asyncio
import random
import time
import logging
from typing import Optional, List, Dict, Any
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class StealthConfig:
    """Stealth 配置"""
    enable_webdriver_removal: bool = True
    enable_chrome_runtime: bool = True
    enable_permissions_mock: bool = True
    enable_language_mock: bool = True
    enable_platform_mock: bool = True
    enable_plugins_mock: bool = True
    humanize_mouse: bool = True
    humanize_typing: bool = True
    random_delay_range: tuple = (0.1, 0.5)  # 随机延迟范围


class StealthMode:
    """
    Stealth 模式：隐藏自动化特征
    
    模拟真实浏览器行为，降低被检测风险
    """
    
    def __init__(self, session, config: StealthConfig = None):
        self.session = session
        self.config = config or StealthConfig()
        self._applied = False
    
    async def apply(self) -> bool:
        """
        应用所有 stealth 脚本
        
        Returns:
            bool: 是否成功应用
        """
        if self._applied:
            logger.debug("Stealth 模式已应用，跳过")
            return True
        
        try:
            # 1. 移除 navigator.webdriver
            if self.config.enable_webdriver_removal:
                await self._remove_webdriver()
            
            # 2. 模拟 Chrome runtime
            if self.config.enable_chrome_runtime:
                await self._mock_chrome_runtime()
            
            # 3. 模拟 permissions.query
            if self.config.enable_permissions_mock:
                await self._mock_permissions()
            
            # 4. 模拟真实语言
            if self.config.enable_language_mock:
                await self._mock_language()
            
            # 5. 模拟真实平台
            if self.config.enable_platform_mock:
                await self._mock_platform()
            
            # 6. 模拟插件
            if self.config.enable_plugins_mock:
                await self._mock_plugins()
            
            self._applied = True
            logger.info("Stealth 模式应用成功")
            return True
            
        except Exception as e:
            logger.error(f"Stealth 模式应用失败: {e}")
            return False
    
    async def _remove_webdriver(self):
        """移除 navigator.webdriver 属性"""
        js = """
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
            configurable: true
        });
        """
        await self.session.eval_js(js)
        logger.debug("已移除 navigator.webdriver")
    
    async def _mock_chrome_runtime(self):
        """模拟 Chrome runtime 对象"""
        js = """
        window.chrome = {
            runtime: {
                connect: () => ({
                    onMessage: { setListener: () => {} },
                    onDisconnect: { setListener: () => {} }
                }),
                sendMessage: () => Promise.resolve()
            },
            loadTimes: () => ({}),
            csi: () => ({}),
            app: {}
        };
        """
        await self.session.eval_js(js)
        logger.debug("已模拟 Chrome runtime")
    
    async def _mock_permissions(self):
        """模拟 permissions.query"""
        js = """
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
        );
        """
        await self.session.eval_js(js)
        logger.debug("已模拟 permissions.query")
    
    async def _mock_language(self):
        """模拟真实语言设置"""
        js = """
        Object.defineProperty(navigator, 'languages', {
            get: () => ['zh-CN', 'zh', 'en-US', 'en']
        });
        Object.defineProperty(navigator, 'language', {
            get: () => 'zh-CN'
        });
        """
        await self.session.eval_js(js)
        logger.debug("已模拟语言设置")
    
    async def _mock_platform(self):
        """模拟真实平台信息"""
        js = """
        Object.defineProperty(navigator, 'platform', {
            get: () => 'Win32'
        });
        Object.defineProperty(navigator, 'oscpu', {
            get: () => 'Windows NT 10.0; Win64; x64'
        });
        """
        await self.session.eval_js(js)
        logger.debug("已模拟平台信息")
    
    async def _mock_plugins(self):
        """模拟浏览器插件"""
        js = """
        // 模拟 PDF 插件
        const mockPlugin = {
            0: {
                name: 'PDF Viewer',
                filename: 'internal-pdf-viewer',
                description: 'Portable Document Format',
                length: 1
            },
            length: 1
        };
        
        Object.defineProperty(navigator, 'plugins', {
            get: () => mockPlugin
        });
        
        // 模拟 mimeTypes
        Object.defineProperty(navigator, 'mimeTypes', {
            get: () => ({
                0: {
                    type: 'application/pdf',
                    description: 'Portable Document Format',
                    suffixes: 'pdf',
                    enabledPlugin: mockPlugin[0]
                },
                length: 1
            })
        });
        """
        await self.session.eval_js(js)
        logger.debug("已模拟插件信息")
    
    # =========================================================================
    # 人类行为模拟
    # =========================================================================
    
    async def human_like_click(
        self,
        x: float,
        y: float,
        duration: float = 0.3,
        steps: int = 20
    ):
        """
        模拟人类点击（带随机移动轨迹）
        
        Args:
            x: 目标 X 坐标
            y: 目标 Y 坐标
            duration: 移动持续时间（秒）
            steps: 移动步数
        """
        if not self.config.humanize_mouse:
            # 直接点击
            await self.session.send("Input.dispatchMouseEvent", {
                "type": "mousePressed",
                "x": x,
                "y": y,
                "button": "left",
                "clickCount": 1
            })
            await self.session.send("Input.dispatchMouseEvent", {
                "type": "mouseReleased",
                "x": x,
                "y": y,
                "button": "left",
                "clickCount": 1
            })
            return
        
        # 从随机起点开始
        start_x = x + random.uniform(-50, 50)
        start_y = y + random.uniform(-50, 50)
        
        # 贝塞尔曲线插值
        for i in range(steps + 1):
            t = i / steps
            # 添加随机扰动
            jitter_x = random.gauss(0, 3) * (1 - abs(2*t - 1))
            jitter_y = random.gauss(0, 3) * (1 - abs(2*t - 1))
            
            current_x = start_x + (x - start_x) * t + jitter_x
            current_y = start_y + (y - start_y) * t + jitter_y
            
            await self.session.send("Input.dispatchMouseEvent", {
                "type": "mouseMoved",
                "x": current_x,
                "y": current_y
            })
            
            await asyncio.sleep(duration / steps)
        
        # 点击
        await self.session.send("Input.dispatchMouseEvent", {
            "type": "mousePressed",
            "x": x,
            "y": y,
            "button": "left",
            "clickCount": 1
        })
        await asyncio.sleep(random.uniform(0.05, 0.1))
        await self.session.send("Input.dispatchMouseEvent", {
            "type": "mouseReleased",
            "x": x,
            "y": y,
            "button": "left",
            "clickCount": 1
        })
        
        logger.debug(f"人类化点击: ({x}, {y})")
    
    async def human_like_type(
        self,
        text: str,
        min_delay: float = 0.05,
        max_delay: float = 0.15
    ):
        """
        模拟人类打字（随机延迟）
        
        Args:
            text: 要输入的文本
            min_delay: 最小延迟（秒）
            max_delay: 最大延迟（秒）
        """
        if not self.config.humanize_typing:
            # 直接输入
            for ch in text:
                await self.session.send("Input.dispatchKeyEvent", {
                    "type": "keyDown",
                    "key": ch,
                    "text": ch
                })
                await self.session.send("Input.dispatchKeyEvent", {
                    "type": "char",
                    "text": ch,
                    "unmodifiedText": ch,
                    "key": ch
                })
                await self.session.send("Input.dispatchKeyEvent", {
                    "type": "keyUp",
                    "key": ch
                })
            return
        
        for ch in text:
            # 随机延迟（模拟人类打字节奏）
            delay = random.uniform(min_delay, max_delay)
            
            # 偶尔添加停顿（模拟思考）
            if random.random() < 0.1:
                delay *= random.uniform(2, 5)
            
            await self.session.send("Input.dispatchKeyEvent", {
                "type": "keyDown",
                "key": ch,
                "text": ch
            })
            await asyncio.sleep(delay * 0.7)
            await self.session.send("Input.dispatchKeyEvent", {
                "type": "char",
                "text": ch,
                "unmodifiedText": ch,
                "key": ch
            })
            await asyncio.sleep(delay * 0.3)
            await self.session.send("Input.dispatchKeyEvent", {
                "type": "keyUp",
                "key": ch
            })
        
        logger.debug(f"人类化输入: {text[:50]}...")
    
    async def human_like_scroll(
        self,
        delta_y: float,
        duration: float = 0.5,
        steps: int = 10
    ):
        """
        模拟人类滚动（平滑曲线）
        
        Args:
            delta_y: 滚动距离
            duration: 滚动持续时间
            steps: 滚动步数
        """
        for i in range(steps + 1):
            t = i / steps
            # 缓动函数（ease-in-out）
            ease_t = 2 * t * t if t < 0.5 else 1 - pow(-2 * t + 2, 2) / 2
            
            current_delta = delta_y * ease_t
            
            await self.session.send("Input.dispatchMouseEvent", {
                "type": "mouseWheel",
                "x": 400,
                "y": 300,
                "deltaX": 0,
                "deltaY": current_delta / steps
            })
            
            await asyncio.sleep(duration / steps)
        
        logger.debug(f"人类化滚动: {delta_y}px")
    
    async def random_delay(self, min_seconds: float = None, max_seconds: float = None):
        """
        随机延迟（模拟人类思考时间）
        
        Args:
            min_seconds: 最小延迟
            max_seconds: 最大延迟
        """
        min_sec = min_seconds or self.config.random_delay_range[0]
        max_sec = max_seconds or self.config.random_delay_range[1]
        
        delay = random.uniform(min_sec, max_sec)
        await asyncio.sleep(delay)
        
        logger.debug(f"随机延迟: {delay:.2f}s")
    
    async def random_human_delay(self):
        """随机人类化延迟（0.5-3 秒）"""
        delay = random.uniform(0.5, 3.0)
        await asyncio.sleep(delay)
        logger.debug(f"人类化延迟: {delay:.2f}s")
    
    # =========================================================================
    # 请求间隔控制
    # =========================================================================
    
    async def throttled_request(
        self,
        func,
        *args,
        min_delay: float = 0.5,
        max_delay: float = 2.0,
        **kwargs
    ):
        """
        带随机间隔的请求执行
        
        Args:
            func: 要执行的异步函数
            min_delay: 最小间隔
            max_delay: 最大间隔
        """
        # 执行前延迟
        await self.random_delay(min_delay, max_delay)
        
        # 执行请求
        result = await func(*args, **kwargs)
        
        # 执行后延迟
        await self.random_delay(min_delay, max_delay)
        
        return result
    
    # =========================================================================
    # 用户代理轮换
    # =========================================================================
    
    USER_AGENTS = [
        # Chrome Windows
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/118.0.0.0 Safari/537.36",
        # Chrome Mac
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        # Firefox
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:121.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:121.0) Gecko/20100101 Firefox/121.0",
        # Safari
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.2 Safari/605.1.15",
    ]
    
    def get_random_user_agent(self) -> str:
        """获取随机用户代理"""
        return random.choice(self.USER_AGENTS)
    
    async def set_user_agent(self, user_agent: str = None):
        """
        设置用户代理
        
        Args:
            user_agent: 用户代理字符串（可选，默认随机）
        """
        ua = user_agent or self.get_random_user_agent()
        
        js = f"""
        Object.defineProperty(navigator, 'userAgent', {{
            get: () => '{ua}'
        }});
        """
        await self.session.eval_js(js)
        logger.debug(f"已设置 User-Agent: {ua[:50]}...")
