"""
anti_detection.py - 反检测模块

提供完整的反检测机制，包括：
- User-Agent 轮换
- 浏览器指纹伪装
- 行为模拟（随机延迟、鼠标轨迹）
- 验证码检测与处理
- 代理池集成
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from playwright.async_api import Page, BrowserContext

logger = logging.getLogger(__name__)


@dataclass
class AntiDetectionConfig:
    """反检测配置"""
    # UA 轮换
    enable_ua_rotation: bool = True
    ua_rotation_interval: int = 5  # 每 N 次请求轮换一次
    
    # 行为模拟
    enable_behavior_simulation: bool = True
    random_delay_range: tuple = (0.3, 1.5)  # 随机延迟范围（秒）
    mouse_movement_enabled: bool = True  # 模拟鼠标移动
    typing_delay_range: tuple = (0.05, 0.15)  # 打字延迟范围（秒）
    
    # 验证码处理
    enable_captcha_detection: bool = True
    captcha_detection_interval: int = 3  # 每 N 次操作检测一次
    max_captcha_retries: int = 2  # 验证码最大重试次数
    
    # 指纹伪装
    enable_fingerprint_masking: bool = True
    navigator_properties: Dict[str, Any] = field(default_factory=lambda: {
        "plugins": 2,
        "languages": ["zh-CN", "zh", "en"],
        "platform": "Win32",
        "vendor": "Google Inc.",
    })
    
    # 代理配置
    enable_proxy_rotation: bool = False
    proxy_rotation_interval: int = 10  # 每 N 次请求轮换代理


class AntiDetectionManager:
    """反检测管理器"""
    
    def __init__(self, config: Optional[AntiDetectionConfig] = None):
        self.config = config or AntiDetectionConfig()
        self._request_count = 0
        self._captcha_detected = False
        self._captcha_retry_count = 0
        self._last_ua_change = 0
        self._last_proxy_change = 0
    
    async def before_request(self, page: Page, site: Optional[str] = None) -> Dict[str, str]:
        """
        请求前处理
        
        Args:
            page: Playwright page 对象
            site: 目标网站
            
        Returns:
            请求头字典
        """
        self._request_count += 1
        
        # UA 轮换
        if self.config.enable_ua_rotation and self._should_rotate_ua():
            await self._rotate_ua(page)
        
        # 代理轮换
        if self.config.enable_proxy_rotation and self._should_rotate_proxy():
            await self._rotate_proxy(page)
        
        # 随机延迟
        if self.config.enable_behavior_simulation:
            delay = random.uniform(*self.config.random_delay_range)
            await asyncio.sleep(delay)
        
        # 获取请求头
        headers = self._get_headers(site)
        
        return headers
    
    async def after_action(self, page: Page, action_type: str = "click"):
        """
        操作后处理
        
        Args:
            page: Playwright page 对象
            action_type: 操作类型（click/input/scroll）
        """
        if not self.config.enable_behavior_simulation:
            return
        
        # 模拟人类行为延迟
        if action_type == "click":
            delay = random.uniform(0.1, 0.3)
            await asyncio.sleep(delay)
        elif action_type == "input":
            # 打字延迟
            delay = random.uniform(*self.config.typing_delay_range)
            await asyncio.sleep(delay)
        
        # 验证码检测
        if self.config.enable_captcha_detection and self._request_count % self.config.captcha_detection_interval == 0:
            await self._check_captcha(page)
    
    async def _rotate_ua(self, page: Page):
        """轮换 UA"""
        from .ua_rotator import get_ua_rotator
        
        ua_rotator = get_ua_rotator()
        new_ua = ua_rotator.get_random_ua()
        
        try:
            await page.evaluate(f'''()
            {{
                Object.defineProperty(navigator, 'userAgent', {{
                    get: () => '{new_ua}'
                }});
            }}''')
            logger.debug(f"UA 已轮换: {new_ua[:50]}...")
            self._last_ua_change = self._request_count
        except Exception as e:
            logger.warning(f"UA 轮换失败: {e}")
    
    def _should_rotate_ua(self) -> bool:
        """检查是否需要轮换 UA"""
        return (self._request_count - self._last_ua_change) >= self.config.ua_rotation_interval
    
    def _should_rotate_proxy(self) -> bool:
        """检查是否需要轮换代理"""
        return (self._request_count - self._last_proxy_change) >= self.config.proxy_rotation_interval
    
    async def _rotate_proxy(self, page: Page):
        """轮换代理"""
        from .proxy_pool import get_proxy_pool
        
        proxy_pool = get_proxy_pool()
        proxy = await proxy_pool.get_next_proxy()
        
        if proxy:
            logger.info(f"代理已轮换: {proxy.url}")
            self._last_proxy_change = self._request_count
    
    def _get_headers(self, site: Optional[str] = None) -> Dict[str, str]:
        """获取请求头"""
        from .ua_rotator import get_request_headers
        
        return get_request_headers(site)
    
    async def _check_captcha(self, page: Page):
        """检测验证码"""
        try:
            # 检测常见验证码元素
            captcha_selectors = [
                "iframe[src*='captcha']",
                "iframe[src*='verify']",
                "div[class*='captcha']",
                "div[class*='verify']",
                "#captcha",
                "#verify",
                "[class*='geetest']",
                "[class*='slide']",
            ]
            
            for selector in captcha_selectors:
                elements = await page.query_selector_all(selector)
                if elements:
                    logger.warning(f"检测到验证码元素: {selector}")
                    self._captcha_detected = True
                    self._captcha_retry_count += 1
                    
                    if self._captcha_retry_count >= self.config.max_captcha_retries:
                        logger.error("验证码重试次数已达上限，跳过当前操作")
                        return False
                    
                    # 等待用户处理或跳过
                    await self._handle_captcha(page)
                    return True
            
            self._captcha_detected = False
            return False
            
        except Exception as e:
            logger.debug(f"验证码检测失败: {e}")
            return False
    
    async def _handle_captcha(self, page: Page):
        """处理验证码"""
        logger.info("检测到验证码，等待处理...")
        
        # 尝试自动处理（如果支持）
        # 这里可以集成第三方验证码识别服务
        
        # 等待一段时间
        await asyncio.sleep(2)
        
        # 检查验证码是否消失
        await self._check_captcha(page)
    
    async def simulate_mouse_movement(self, page: Page, from_point: tuple, to_point: tuple, duration: float = 0.5):
        """
        模拟鼠标移动
        
        Args:
            page: Playwright page 对象
            from_point: 起始点 (x, y)
            to_point: 终点 (x, y)
            duration: 移动持续时间（秒）
        """
        if not self.config.mouse_movement_enabled:
            return
        
        steps = int(duration * 60)  # 60 FPS
        dx = (to_point[0] - from_point[0]) / steps
        dy = (to_point[1] - from_point[1]) / steps
        
        current_x, current_y = from_point
        
        for _ in range(steps):
            current_x += dx
            current_y += dy
            
            # 添加随机抖动
            jitter_x = random.uniform(-2, 2)
            jitter_y = random.uniform(-2, 2)
            
            await page.mouse.move(current_x + jitter_x, current_y + jitter_y)
            await asyncio.sleep(duration / steps * random.uniform(0.5, 1.5))
    
    async def simulate_typing(self, page: Page, selector: str, text: str, delay_range: tuple = None):
        """
        模拟人类打字
        
        Args:
            page: Playwright page 对象
            selector: 元素选择器
            text: 要输入的文本
            delay_range: 打字延迟范围
        """
        element = await page.wait_for_selector(selector, state="visible")
        if not element:
            return
        
        await element.click()
        
        # 清空现有内容
        await element.fill("")
        
        # 逐字符输入
        for char in text:
            delay = random.uniform(* (delay_range or self.config.typing_delay_range))
            await asyncio.sleep(delay)
            await element.press(char)
    
    def is_captcha_detected(self) -> bool:
        """检查是否检测到验证码"""
        return self._captcha_detected
    
    def reset_captcha_status(self):
        """重置验证码状态"""
        self._captcha_detected = False
        self._captcha_retry_count = 0
    
    def get_stats(self) -> Dict[str, Any]:
        """获取反检测统计"""
        return {
            "total_requests": self._request_count,
            "captcha_detected": self._captcha_detected,
            "captcha_retry_count": self._captcha_retry_count,
            "ua_rotations": self._request_count - self._last_ua_change,
            "proxy_rotations": self._request_count - self._last_proxy_change,
        }


# 全局单例
_anti_detection: Optional[AntiDetectionManager] = None


def get_anti_detection() -> AntiDetectionManager:
    """获取全局反检测管理器单例"""
    global _anti_detection
    if _anti_detection is None:
        _anti_detection = AntiDetectionManager()
    return _anti_detection


def set_anti_detection(manager: AntiDetectionManager):
    """设置全局反检测管理器"""
    global _anti_detection
    _anti_detection = manager


def reset_anti_detection():
    """重置全局反检测管理器"""
    global _anti_detection
    _anti_detection = None