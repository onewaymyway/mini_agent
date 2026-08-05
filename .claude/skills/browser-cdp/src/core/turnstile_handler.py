"""
turnstile_handler.py - Cloudflare Turnstile 验证码处理模块

支持 Cloudflare Turnstile 验证码的自动求解
集成第三方服务：2Captcha、CapMonster
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass
class TurnstileResult:
    """Turnstile 验证码处理结果"""
    success: bool
    token: Optional[str] = None
    message: str = ""
    
    def __str__(self):
        status = "成功" if self.success else "失败"
        return f"[{status}] Turnstile, token={'存在' if self.token else '无'}, 消息={self.message}"


class TurnstileHandler:
    """
    Cloudflare Turnstile 验证码处理器
    
    支持通过第三方服务自动求解 Turnstile 验证码
    """
    
    # Turnstile 检测选择器
    SELECTORS = [
        "[data-sitekey]",
        ".cf-turnstile",
        "iframe[src*='turnstile']",
        "#cf-turnstile",
        "[class*='turnstile']",
    ]
    
    def __init__(self, session, service: str = "2captcha", api_key: Optional[str] = None):
        """
        Args:
            session: CDP session 对象
            service: 验证码服务 (2captcha, capmonster)
            api_key: API Key（可选，默认从环境变量读取）
        """
        self.session = session
        self.service = service
        self.api_key = api_key or self._get_api_key(service)
        self._token_cache: dict = {}
    
    def _get_api_key(self, service: str) -> Optional[str]:
        """从环境变量获取 API Key"""
        env_vars = {
            "2captcha": "2CAPTCHA_API_KEY",
            "capmonster": "CAPMONSTER_API_KEY",
        }
        return os.getenv(env_vars.get(service, ""))
    
    async def detect_turnstile(self) -> bool:
        """
        检测页面是否存在 Turnstile 验证码
        
        Returns:
            bool: 是否检测到 Turnstile
        """
        # 检查 URL
        url = await self.session.get_current_url()
        if 'turnstile' in url.lower():
            logger.debug(f"URL 包含 turnstile: {url}")
            return True
        
        # 检查页面元素
        for selector in self.SELECTORS:
            try:
                elements = await self.session.query_selector_all(selector)
                if elements:
                    logger.info(f"检测到 Turnstile 元素: {selector}")
                    return True
            except Exception as e:
                logger.debug(f"检查 {selector} 失败: {e}")
        
        # 检查 iframe
        try:
            iframes = await self.session.query_selector_all("iframe")
            for iframe in iframes:
                src = await iframe.get_attribute("src")
                if src and 'turnstile' in src.lower():
                    logger.info("检测到 Turnstile iframe")
                    return True
        except Exception as e:
            logger.debug(f"检查 iframe 失败: {e}")
        
        return False
    
    async def get_sitekey(self) -> Optional[str]:
        """
        获取 Turnstile sitekey
        
        Returns:
            sitekey 字符串或 None
        """
        try:
            # 尝试从 data-sitekey 属性获取
            sitekey = await self.session.eval_js("""
                () => {
                    const el = document.querySelector('[data-sitekey]');
                    return el ? el.dataset.sitekey : null;
                }
            """)
            if sitekey:
                return sitekey
            
            # 尝试从 iframe src 获取
            iframes = await self.session.query_selector_all("iframe")
            for iframe in iframes:
                src = await iframe.get_attribute("src")
                if src and 'turnstile' in src.lower():
                    # 从 URL 中提取 sitekey
                    import re
                    match = re.search(r'sitekey=([^&]+)', src)
                    if match:
                        return match.group(1)
            
            return None
        except Exception as e:
            logger.error(f"获取 sitekey 失败: {e}")
            return None
    
    async def solve(self, timeout: int = 120) -> TurnstileResult:
        """
        求解 Turnstile 验证码
        
        Args:
            timeout: 超时时间（秒）
        
        Returns:
            TurnstileResult: 处理结果
        """
        # 1. 检测 Turnstile
        if not await self.detect_turnstile():
            return TurnstileResult(
                success=False,
                message="未检测到 Turnstile 验证码"
            )
        
        # 2. 获取 sitekey
        sitekey = await self.get_sitekey()
        if not sitekey:
            return TurnstileResult(
                success=False,
                message="无法获取 sitekey"
            )
        
        logger.info(f"开始求解 Turnstile, sitekey={sitekey[:8]}...")
        
        # 3. 根据服务求解
        if self.service == "2captcha":
            return await self._solve_via_2captcha(sitekey, timeout)
        elif self.service == "capmonster":
            return await self._solve_via_capmonster(sitekey, timeout)
        else:
            return TurnstileResult(
                success=False,
                message=f"不支持的服务: {self.service}"
            )
    
    async def _solve_via_2captcha(self, sitekey: str, timeout: int) -> TurnstileResult:
        """通过 2Captcha 服务求解"""
        try:
            # 1. 提交任务
            task_id = await self._submit_to_2captcha(sitekey)
            
            # 2. 轮询结果
            token = await self._poll_2captcha_result(task_id, timeout)
            
            if token:
                # 3. 注入 token
                await self._inject_token(token)
                return TurnstileResult(
                    success=True,
                    token=token,
                    message="Turnstile 求解成功"
                )
            else:
                return TurnstileResult(
                    success=False,
                    message="2Captcha 求解超时"
                )
        except Exception as e:
            logger.error(f"2Captcha 求解失败: {e}")
            return TurnstileResult(
                success=False,
                message=f"求解失败: {str(e)}"
            )
    
    async def _submit_to_2captcha(self, sitekey: str) -> str:
        """提交任务到 2Captcha"""
        import urllib.request
        import urllib.parse
        import json
        
        url = "https://2captcha.com/in.php"
        params = urllib.parse.urlencode({
            "key": self.api_key,
            "method": "turnstile",
            "sitekey": sitekey,
            "pageurl": await self.session.get_current_url(),
            "json": "1",
        })
        
        req = urllib.request.Request(
            f"{url}?{params}",
            headers={"User-Agent": "2Captcha-Python/1.0"}
        )
        
        with urllib.request.urlopen(req, timeout=30) as response:
            data = json.loads(response.read().decode())
            
        if data.get("status") != 1:
            raise Exception(f"2Captcha 提交失败: {data.get('request')}")
        
        return data["request"]
    
    async def _poll_2captcha_result(self, task_id: str, timeout: int) -> Optional[str]:
        """轮询 2Captcha 结果"""
        import urllib.request
        import urllib.parse
        import json
        
        url = "https://2captcha.com/res.php"
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            params = urllib.parse.urlencode({
                "key": self.api_key,
                "action": "get",
                "id": task_id,
                "json": "1",
            })
            
            req = urllib.request.Request(
                f"{url}?{params}",
                headers={"User-Agent": "2Captcha-Python/1.0"}
            )
            
            with urllib.request.urlopen(req, timeout=30) as response:
                data = json.loads(response.read().decode())
            
            if data.get("status") == 1:
                return data["request"]
            elif data.get("status") == 0:
                # 还在处理中，等待后重试
                await asyncio.sleep(5)
            else:
                raise Exception(f"2Captcha 轮询失败: {data.get('request')}")
        
        return None
    
    async def _inject_token(self, token: str):
        """将 token 注入到页面"""
        js = f"""
        () => {{
            // 查找 Turnstile 容器
            const containers = document.querySelectorAll('.cf-turnstile, [data-sitekey]');
            containers.forEach(container => {{
                // 设置 token
                if (container.setAttribute) {{
                    container.setAttribute('data-callback', 'onTurnstileSuccess');
                }}
                // 尝试通过 window 对象设置
                if (window.turnstile) {{
                    window.turnstile.ready(() => {{
                        // 触发回调
                        const callback = container.dataset.callback || 'onTurnstileSuccess';
                        if (window[callback]) {{
                            window[callback](token);
                        }}
                    }});
                }}
            }});
            
            // 尝试直接设置 token
            const iframe = document.querySelector('iframe[src*="turnstile"]');
            if (iframe) {{
                try {{
                    iframe.contentWindow.postMessage({{ token }}, '*');
                }} catch (e) {{
                    // 跨域，忽略
                }}
            }}
        }}
        """
        await self.session.eval_js(js)
        logger.info("Token 已注入")
    
    async def _solve_via_capmonster(self, sitekey: str, timeout: int) -> TurnstileResult:
        """通过 CapMonster 服务求解"""
        # TODO: 实现 CapMonster 集成
        return TurnstileResult(
            success=False,
            message="CapMonster 集成待实现"
        )


# 便捷函数
async def detect_and_solve_turnstile(session, service: str = "2captcha", api_key: Optional[str] = None, timeout: int = 120) -> TurnstileResult:
    """
    检测并求解 Turnstile 验证码的便捷函数
    
    Args:
        session: CDP session 对象
        service: 验证码服务
        api_key: API Key
        timeout: 超时时间
    
    Returns:
        TurnstileResult: 处理结果
    """
    handler = TurnstileHandler(session, service, api_key)
    return await handler.solve(timeout)
