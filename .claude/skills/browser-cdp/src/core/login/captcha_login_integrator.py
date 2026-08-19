"""
captcha_login_integrator.py - 验证码与登录流程集成模块

将 CaptchaHandler 集成到 LoginFlowManager 中，支持：
- 登录流程中自动检测并处理验证码
- 验证码类型自动识别
- 人机验证页面等待人工介入
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional, List, Dict, Any, Callable

from .login_state_detector_v2 import LoginStateDetectorV2, LoginState
from ..captcha_handler import CaptchaHandler, CaptchaType, CaptchaResult

logger = logging.getLogger(__name__)


@dataclass
class CaptchaLoginResult:
    """验证码登录结果"""
    success: bool
    captcha_handled: bool
    captcha_type: Optional[CaptchaType] = None
    captcha_result: Optional[CaptchaResult] = None
    login_state: Optional[LoginState] = None
    message: str = ""
    needs_human_intervention: bool = False


class CaptchaLoginIntegrator:
    """
    验证码与登录流程集成器
    
    在登录流程中自动检测并处理验证码
    """
    
    # 常见验证码页面特征
    CAPTCHA_PAGE_SELECTORS = [
        ".geetest_widget", ".rcaptcha", ".hcaptcha",
        "input[class*='captcha']", "input[name*='captcha']",
        ".slide-to-fill", ".verify-code",
        "[class*='geetest']", "[class*='nc']",
    ]
    
    # 验证码文本模式
    CAPTCHA_TEXT_PATTERNS = [
        'captcha', 'verify', '验证', '滑块', '拼图', '点选',
        'recaptcha', 'hcaptcha', 'geetest', 'captcha image',
    ]
    
    def __init__(self, session, captcha_handler: Optional[CaptchaHandler] = None):
        self.session = session
        self.captcha_handler = captcha_handler or CaptchaHandler(session)
        self.login_detector = LoginStateDetectorV2(session)
    
    async def handle_captcha_during_login(
        self,
        login_func: Callable,
        *args,
        timeout: float = 60.0,
        **kwargs,
    ) -> CaptchaLoginResult:
        """
        在登录流程中处理验证码
        
        Args:
            login_func: 登录函数（可能是异步的）
            *args, **kwargs: 传递给登录函数的参数
            timeout: 总超时时间
            
        Returns:
            CaptchaLoginResult
        """
        import asyncio
        import time
        
        start_time = time.time()
        
        # 步骤1: 尝试登录
        try:
            if asyncio.iscoroutinefunction(login_func):
                await login_func(*args, **kwargs)
            else:
                login_func(*args, **kwargs)
        except Exception as e:
            logger.warning(f"登录过程异常: {e}")
        
        # 步骤2: 检测验证码
        captcha_info = await self._detect_captcha()
        
        if not captcha_info['detected']:
            # 无验证码，检查登录状态
            login_state = self.login_detector.check_login_state()
            return CaptchaLoginResult(
                success=login_state.is_logged_in,
                captcha_handled=False,
                login_state=login_state,
                message="登录成功（无验证码）" if login_state.is_logged_in else "登录失败（无验证码）",
            )
        
        # 步骤3: 处理验证码
        captcha_type = captcha_info.get('type', CaptchaType.UNKNOWN)
        logger.info(f"检测到验证码: {captcha_type}")
        
        if captcha_type == CaptchaType.RECAPTCHA or captcha_type == CaptchaType.HCAPTCHA:
            # 人机验证，需要人工介入
            return CaptchaLoginResult(
                success=False,
                captcha_handled=False,
                captcha_type=captcha_type,
                needs_human_intervention=True,
                message=f"检测到 {captcha_type.value}，需要人工完成验证",
                login_state=self.login_detector.check_login_state(),
            )
        
        # 自动处理其他类型
        try:
            captcha_result = await self.captcha_handler.handle_captcha(
                captcha_type=captcha_type,
                timeout=timeout - (time.time() - start_time),
            )
            
            if captcha_result.success:
                logger.info(f"验证码处理成功: {captcha_result}")
                # 重新尝试登录
                try:
                    if asyncio.iscoroutinefunction(login_func):
                        await login_func(*args, **kwargs)
                    else:
                        login_func(*args, **kwargs)
                except Exception as e:
                    logger.warning(f"验证码后登录失败: {e}")
                
                login_state = self.login_detector.check_login_state()
                return CaptchaLoginResult(
                    success=login_state.is_logged_in,
                    captcha_handled=True,
                    captcha_type=captcha_type,
                    captcha_result=captcha_result,
                    login_state=login_state,
                    message="验证码处理后登录成功" if login_state.is_logged_in else "验证码处理后仍登录失败",
                )
            else:
                return CaptchaLoginResult(
                    success=False,
                    captcha_handled=False,
                    captcha_type=captcha_type,
                    captcha_result=captcha_result,
                    login_state=self.login_detector.check_login_state(),
                    message=f"验证码处理失败: {captcha_result.message}",
                )
        
        except Exception as e:
            logger.error(f"验证码处理异常: {e}")
            return CaptchaLoginResult(
                success=False,
                captcha_handled=False,
                captcha_type=captcha_type,
                login_state=self.login_detector.check_login_state(),
                message=f"验证码处理异常: {str(e)}",
            )
    
    async def _detect_captcha(self) -> Dict[str, Any]:
        """
        检测页面上是否有验证码
        
        Returns:
            dict: {detected, type, confidence, details}
        """
        js = """
        (function() {
            var result = {
                detected: false,
                type: 'unknown',
                confidence: 0,
                details: []
            };
            
            // 检测常见的验证码容器
            var captchaContainers = [
                '.geetest_widget', '.rcaptcha', '.hcaptcha',
                '[class*="captcha"]', '[id*="captcha"]',
                '[class*="verify"]', '[id*="verify"]',
                '.slide-to-fill', '.nc_container',
                '[class*="geetest"]', '[class*="recaptcha"]',
                '[class*="hcaptcha"]', '[name*="captcha"]'
            ];
            
            var containerFound = false;
            captchaContainers.forEach(function(sel) {
                try {
                    if (document.querySelector(sel)) {
                        containerFound = true;
                        result.details.push('container:' + sel);
                    }
                } catch(e) {}
            });
            
            // 检测验证码 iframe
            var iframes = document.querySelectorAll('iframe');
            iframes.forEach(function(iframe) {
                var src = iframe.src || '';
                if (src.indexOf('recaptcha') !== -1 || src.indexOf('hcaptcha') !== -1 ||
                    src.indexOf('geetest') !== -1) {
                    result.type = src.indexOf('recaptcha') !== -1 ? 'recaptcha' :
                                  src.indexOf('hcaptcha') !== -1 ? 'hcaptcha' : 'geetest';
                    result.confidence = 0.9;
                    result.detected = true;
                    result.details.push('iframe:' + result.type);
                }
            });
            
            // 检测验证码输入框
            var inputs = document.querySelectorAll('input[type="text"], input[type="number"]');
            inputs.forEach(function(input) {
                var ph = (input.placeholder || '').toLowerCase();
                var name = (input.name || '').toLowerCase();
                if (ph.indexOf('captcha') !== -1 || ph.indexOf('verify') !== -1 ||
                    name.indexOf('captcha') !== -1 || name.indexOf('code') !== -1) {
                    if (!result.detected) {
                        result.detected = true;
                        result.type = 'text_captcha';
                        result.confidence = 0.7;
                    }
                    result.details.push('input:' + (ph || name));
                }
            });
            
            // 检测验证码图片
            var imgs = document.querySelectorAll('img');
            imgs.forEach(function(img) {
                var src = img.src || '';
                var alt = (img.alt || '').toLowerCase();
                if (src.indexOf('captcha') !== -1 || alt.indexOf('captcha') !== -1 ||
                    alt.indexOf('verify') !== -1) {
                    if (!result.detected) {
                        result.detected = true;
                        result.type = 'image_captcha';
                        result.confidence = 0.6;
                    }
                    result.details.push('img:captcha');
                }
            });
            
            // 检测页面文本中的验证码关键词
            var bodyText = document.body.innerText.toLowerCase();
            var keywords = ['请输入验证码', 'captcha', 'verify code', 'input code', '滑动验证', '点击验证'];
            keywords.forEach(function(kw) {
                if (bodyText.indexOf(kw) !== -1) {
                    result.details.push('text:' + kw);
                }
            });
            
            // 综合判断
            if (containerFound || result.details.length > 0) {
                result.detected = true;
                if (result.confidence < 0.5) {
                    result.confidence = Math.min(0.5 + result.details.length * 0.1, 0.9);
                }
            }
            
            return result;
        })()
        """
        try:
            result = await self.session.eval_js(js)
            return result
        except Exception as e:
            logger.debug(f"验证码检测失败: {e}")
            return {'detected': False, 'type': 'unknown', 'confidence': 0}
    
    def wait_for_2fa(self, timeout: float = 60.0) -> LoginState:
        """
        等待 2FA 验证码输入
        
        Args:
            timeout: 超时时间（秒）
        
        Returns:
            LoginState: 检测到的状态
        """
        import time
        start_time = time.time()
        
        while time.time() - start_time < timeout:
            state = self.login_detector.check_login_state()
            
            # 如果检测到 2FA 特征
            if '2fa_detected' in state.detected_features or state.score < 30:
                logger.info(f"检测到 2FA 页面，等待用户输入... (score={state.score})")
                return state
            
            # 如果已登录
            if state.is_logged_in:
                return state
            
            time.sleep(1.0)
        
        return LoginState(is_logged_in=False, confidence=0.0, score=0, method="2fa_timeout")


# 便捷函数
async def integrate_captcha_with_login(session, login_func, *args, **kwargs) -> CaptchaLoginResult:
    """集成验证码处理的登录便捷函数"""
    integrator = CaptchaLoginIntegrator(session)
    return await integrator.handle_captcha_during_login(login_func, *args, **kwargs)
