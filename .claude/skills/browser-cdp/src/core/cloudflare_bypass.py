#!/usr/bin/env python3
"""
cloudflare_bypass.py - Cloudflare 反检测模块

处理 Cloudflare 保护网站的访问，包括：
- 5秒挑战页（5秒盾）
- CAPTCHA 验证
- 浏览器指纹检测
- JS 挑战
"""
from __future__ import annotations

import asyncio
import logging
import random
import time
from typing import Optional, Dict, Any, List
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class CloudflareConfig:
    """Cloudflare 绕过配置"""
    enable_js_bypass: bool = True
    enable_fingerprint_bypass: bool = True
    enable_captcha_detection: bool = True
    wait_timeout: int = 30
    max_retries: int = 3
    random_delay_range: tuple = (1, 3)


class CloudflareBypass:
    """
    Cloudflare 反检测处理器
    
    处理 Cloudflare 保护网站的访问挑战
    """
    
    # Cloudflare 检测选择器
    SELECTORS = {
        "challenge": [
            "#cf-challenge-running",
            ".cf-browser-verification",
            "#cf-content",
            "[name='cf-challenge']",
        ],
        "captcha": [
            "#challenge-running",
            ".hcaptcha",
            "[class*='challenge']",
        ],
        "js_challenge": [
            "script[src*='challenge']",
            "#cf-challenge",
        ],
    }
    
    # Cloudflare 检测关键词
    KEYWORDS = [
        "checking your browser",
        "attention required",
        "just a moment",
        "ddos-protection",
        "cloudflare",
        "ray id",
        "ip address",
        "verify you are human",
    ]
    
    def __init__(self, session, config: CloudflareConfig = None):
        self.session = session
        self.config = config or CloudflareConfig()
        self._detected = False
        self._challenge_type: Optional[str] = None
    
    async def detect(self) -> bool:
        """
        检测当前页面是否为 Cloudflare 挑战页
        
        Returns:
            bool: 是否检测到 Cloudflare 保护
        """
        try:
            # 1. 检查 URL
            url = await self.session.get_current_url()
            if 'cloudflare' in url.lower() or 'cf-ray' in url.lower():
                logger.info(f"检测到 Cloudflare URL: {url}")
                self._detected = True
                return True
            
            # 2. 检查页面元素
            for selector_group in self.SELECTORS.values():
                for selector in selector_group:
                    try:
                        elements = await self.session.query_selector_all(selector)
                        if elements:
                            logger.info(f"检测到 Cloudflare 元素: {selector}")
                            self._detected = True
                            self._challenge_type = self._classify_challenge(elements)
                            return True
                    except Exception:
                        continue
            
            # 3. 检查页面文本
            page_text = await self.session.get_page_text()
            for keyword in self.KEYWORDS:
                if keyword in page_text.lower():
                    logger.info(f"检测到 Cloudflare 关键词: {keyword}")
                    self._detected = True
                    self._challenge_type = "text_based"
                    return True
            
            return False
            
        except Exception as e:
            logger.error(f"检测 Cloudflare 失败: {e}")
            return False
    
    async def _classify_challenge(self, elements) -> str:
        """分类挑战类型"""
        for elem in elements:
            try:
                text = await elem.inner_text()
                if 'captcha' in text.lower() or 'hcaptcha' in text.lower():
                    return "captcha"
                elif 'js' in text.lower() or 'javascript' in text.lower():
                    return "js_challenge"
                elif 'second' in text.lower() or 'waiting' in text.lower():
                    return "waiting_challenge"
            except Exception:
                continue
        return "unknown"
    
    async def bypass(self) -> bool:
        """
        尝试绕过 Cloudflare 保护
        
        Returns:
            bool: 是否成功绕过
        """
        if not self._detected:
            return True
        
        logger.info(f"开始绕过 Cloudflare 保护，类型：{self._challenge_type}")
        
        for attempt in range(self.config.max_retries):
            try:
                # 1. 等待挑战页加载
                await self._wait_for_challenge()
                
                # 2. 根据类型处理
                if self._challenge_type == "waiting_challenge":
                    success = await self._handle_waiting_challenge()
                elif self._challenge_type == "js_challenge":
                    success = await self._handle_js_challenge()
                elif self._challenge_type == "captcha":
                    success = await self._handle_captcha_challenge()
                else:
                    success = await self._handle_generic_challenge()
                
                if success:
                    # 验证是否成功
                    await asyncio.sleep(2)
                    is_still_challenged = await self.detect()
                    if not is_still_challenged:
                        logger.info("Cloudflare 绕过成功")
                        self._detected = False
                        return True
                    
                # 等待后重试
                delay = random.uniform(*self.config.random_delay_range)
                logger.info(f"第 {attempt + 1} 次尝试失败，{delay:.1f} 秒后重试")
                await asyncio.sleep(delay)
                
            except Exception as e:
                logger.error(f"绕过 Cloudflare 失败: {e}")
                await asyncio.sleep(1)
        
        logger.warning("Cloudflare 绕过失败，需要手动处理")
        return False
    
    async def _wait_for_challenge(self):
        """等待挑战页加载完成"""
        # 等待页面稳定
        await asyncio.sleep(2)
        
        # 模拟人类行为
        await self.session.evaluate("window.scrollBy(0, 100)")
        await asyncio.sleep(1)
        await self.session.evaluate("window.scrollBy(0, -100)")
    
    async def _handle_waiting_challenge(self) -> bool:
        """处理等待型挑战（5秒盾）"""
        logger.info("检测到等待型挑战，等待自动通过...")
        
        # 等待挑战完成（通常 5-10 秒）
        for i in range(self.config.wait_timeout):
            is_still_challenged = await self.detect()
            if not is_still_challenged:
                logger.info("等待挑战已通过")
                return True
            await asyncio.sleep(1)
        
        return False
    
    async def _handle_js_challenge(self) -> bool:
        """处理 JS 挑战"""
        logger.info("检测到 JS 挑战，执行 JS 验证...")
        
        # 尝试执行页面上的 JS 验证脚本
        try:
            # 查找并执行挑战脚本
            result = await self.session.evaluate("""
                (function() {
                    // 尝试找到并执行挑战脚本
                    const scripts = document.querySelectorAll('script');
                    for (const script of scripts) {
                        if (script.src && script.src.includes('challenge')) {
                            // 加载并执行挑战脚本
                            const newScript = document.createElement('script');
                            newScript.src = script.src;
                            document.head.appendChild(newScript);
                            return 'loaded';
                        }
                    }
                    return 'not_found';
                })()
            """)
            
            if result == 'loaded':
                logger.info("JS 挑战脚本已加载")
                # 等待执行
                await asyncio.sleep(3)
                return True
            
        except Exception as e:
            logger.error(f"处理 JS 挑战失败: {e}")
        
        return False
    
    async def _handle_captcha_challenge(self) -> bool:
        """处理 CAPTCHA 挑战"""
        logger.warning("检测到 CAPTCHA 挑战，需要手动处理或使用第三方服务")
        
        # 截图保存供用户查看
        try:
            screenshot_path = f"temp_data/cf_captcha_{int(time.time())}.png"
            await self.session.screenshot(path=screenshot_path)
            logger.info(f"CAPTCHA 截图已保存：{screenshot_path}")
        except Exception as e:
            logger.error(f"保存 CAPTCHA 截图失败: {e}")
        
        return False
    
    async def _handle_generic_challenge(self) -> bool:
        """处理通用挑战"""
        logger.info("处理通用 Cloudflare 挑战...")
        
        # 尝试点击页面上的按钮
        try:
            buttons = await self.session.query_selector_all("button, input[type='submit']")
            for btn in buttons:
                text = await btn.inner_text()
                if any(kw in text.lower() for kw in ['verify', 'continue', 'submit', 'done']):
                    await btn.click()
                    logger.info(f"已点击按钮：{text}")
                    await asyncio.sleep(2)
                    return True
        except Exception as e:
            logger.error(f"处理通用挑战失败: {e}")
        
        return False
    
    async def apply_stealth(self):
        """应用反检测脚本"""
        if not self.config.enable_fingerprint_bypass:
            return
        
        js = """
        // 隐藏自动化特征
        Object.defineProperty(navigator, 'webdriver', {
            get: () => undefined,
            configurable: true
        });
        
        // 模拟真实浏览器指纹
        const originalQuery = window.navigator.permissions.query;
        window.navigator.permissions.query = (parameters) => (
            parameters.name === 'notifications' ?
                Promise.resolve({ state: Notification.permission }) :
                originalQuery(parameters)
        );
        
        // 隐藏 Chrome Runtime
        delete window.cdc_adio;
        
        // 模拟真实插件
        const mockPlugins = {
            0: {
                name: 'PDF Viewer',
                filename: 'internal-pdf-viewer',
                description: 'Portable Document Format',
                length: 1
            },
            length: 1
        };
        Object.defineProperty(navigator, 'plugins', {
            get: () => mockPlugins
        });
        """
        
        try:
            await self.session.evaluate(js)
            logger.debug("Cloudflare 反检测脚本已应用")
        except Exception as e:
            logger.error(f"应用反检测脚本失败: {e}")
