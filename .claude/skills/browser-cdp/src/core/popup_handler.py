"""
popup_handler.py - 弹窗处理模块

支持处理常见弹窗类型：
- 广告弹窗
- 确认/取消弹窗
- Cookie 同意弹窗
- 登录弹窗
- 通知弹窗
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional, List, Dict, Any

logger = logging.getLogger(__name__)


class PopupType(Enum):
    """弹窗类型"""
    AD = "ad"                      # 广告弹窗
    CONFIRM = "confirm"            # 确认弹窗
    ALERT = "alert"                # 警告弹窗
    PROMPT = "prompt"              # 输入弹窗
    COOKIE = "cookie"              # Cookie 同意弹窗
    LOGIN = "login"                # 登录弹窗
    NOTIFICATION = "notification"  # 通知弹窗
    UNKNOWN = "unknown"            # 未知类型


@dataclass
class PopupInfo:
    """弹窗信息"""
    type: PopupType
    selector: str
    title: str = ""
    message: str = ""
    
    def to_dict(self) -> dict:
        return {
            "type": self.type.value,
            "selector": self.selector,
            "title": self.title,
            "message": self.message,
        }


class PopupHandler:
    """
    弹窗处理器
    
    支持检测和处理常见弹窗
    """
    
    # 常见弹窗选择器
    POPUP_SELECTORS = {
        PopupType.AD: [
            ".ad-modal, .advertisement, [class*='ad-']",
            ".popup-ad, .modal-ad, .banner-ad",
            "[class*='close'] button, [class*='dismiss'] button",
        ],
        PopupType.COOKIE: [
            "#cookie-banner, .cookie-banner, [class*='cookie']",
            "#accept-cookies, .accept-cookies",
        ],
        PopupType.LOGIN: [
            "#login-modal, .login-modal, [class*='login']",
            "#sign-in, .sign-in-modal",
        ],
        PopupType.NOTIFICATION: [
            "#notification-popup, .notification-popup",
            "[class*='notify']",
        ],
    }
    
    # 关闭按钮选择器
    CLOSE_SELECTORS = [
        "[class*='close'] button, [class*='dismiss'] button, [class*='modal-close']",
        ".close, .dismiss, .modal-close",
        "button[aria-label*='close'], button[aria-label*='dismiss']",
        "[role='button'][aria-label*='close']",
    ]
    
    def __init__(self, session):
        self.session = session
        self._popup_history: List[Dict[str, Any]] = []
    
    # =========================================================================
    # 弹窗检测
    # =========================================================================
    
    def detect_popups(self) -> List[PopupInfo]:
        """
        检测页面上的弹窗
        
        Returns:
            List[PopupInfo]: 检测到的弹窗列表
        """
        popups = []
        
        # 检测已知类型的弹窗
        for popup_type, selectors in self.POPUP_SELECTORS.items():
            for selector in selectors:
                try:
                    count = self.session.eval_js(f'''
                        (function() {{
                            const elements = document.querySelectorAll("{selector}");
                            let visible = 0;
                            elements.forEach(function(el) {{
                                const rect = el.getBoundingClientRect();
                                const style = window.getComputedStyle(el);
                                if (rect.width > 0 && rect.height > 0 && 
                                    style.display !== 'none' && style.visibility !== 'hidden') {{
                                    visible++;
                                }}
                            }});
                            return visible;
                        }})()
                    ''')
                    if count > 0:
                        popup = PopupInfo(
                            type=popup_type,
                            selector=selector,
                        )
                        popups.append(popup)
                        logger.info(f"检测到弹窗: {popup_type.value}, 选择器: {selector}")
                except Exception as e:
                    logger.debug(f"检测弹窗失败 {selector}: {e}")
        
        # 检测 JS 弹窗
        alert_popup = self._detect_js_popup()
        if alert_popup:
            popups.append(alert_popup)
        
        return popups
    
    def _detect_js_popup(self) -> Optional[PopupInfo]:
        """检测 JS 弹窗（alert/confirm/prompt）"""
        try:
            has_popup = self.session.eval_js('''
                (function() {
                    return window.confirm.toString().indexOf('[native code]') !== -1 ||
                           window.alert.toString().indexOf('[native code]') !== -1;
                })()
            ''')
            # 注意：CDP 无法直接检测 JS 弹窗，这里只是占位
            return None
        except Exception:
            return None
    
    # =========================================================================
    # 弹窗处理
    # =========================================================================
    
    def handle_popups(self, auto_close: bool = True, timeout: float = 5.0) -> Dict[str, Any]:
        """
        处理页面上的弹窗
        
        Args:
            auto_close: 是否自动关闭弹窗
            timeout: 超时时间（秒）
        
        Returns:
            Dict: 处理结果
        """
        start_time = time.time()
        results = {
            "popups_detected": 0,
            "popups_closed": 0,
            "errors": [],
        }
        
        popups = self.detect_popups()
        results["popups_detected"] = len(popups)
        
        if not popups:
            logger.info("未检测到弹窗")
            return results
        
        logger.info(f"检测到 {len(popups)} 个弹窗，开始处理")
        
        for popup in popups:
            try:
                if auto_close:
                    self._close_popup(popup)
                    results["popups_closed"] += 1
                else:
                    logger.info(f"跳过弹窗: {popup.type.value}")
            except Exception as e:
                error_msg = f"处理弹窗失败 {popup.type.value}: {e}"
                logger.error(error_msg)
                results["errors"].append(error_msg)
            
            # 记录历史
            self._popup_history.append({
                "timestamp": time.time(),
                "popup": popup.to_dict(),
                "action": "closed" if auto_close else "skipped",
            })
            
            # 检查超时
            if time.time() - start_time > timeout:
                logger.warning("处理弹窗超时")
                break
        
        logger.info(f"弹窗处理完成: 检测到 {results['popups_detected']} 个，关闭 {results['popups_closed']} 个")
        return results
    
    def _close_popup(self, popup: PopupInfo) -> None:
        """关闭指定弹窗"""
        # 尝试点击关闭按钮
        for selector in self.CLOSE_SELECTORS:
            try:
                clicked = self.session.eval_js(f'''
                    (function() {{
                        const buttons = document.querySelectorAll("{selector}");
                        for (const btn of buttons) {{
                            const rect = btn.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0) {{
                                btn.click();
                                return true;
                            }}
                        }}
                        return false;
                    }})()
                ''')
                if clicked:
                    logger.debug(f"点击关闭按钮: {selector}")
                    time.sleep(0.3)
                    return
            except Exception as e:
                logger.debug(f"点击关闭按钮失败 {selector}: {e}")
        
        # 尝试按 Escape 键
        try:
            self.session.eval_js('''
                (function() {
                    const event = new KeyboardEvent('keydown', {key: 'Escape', code: 'Escape', keyCode: 27});
                    document.dispatchEvent(event);
                    return true;
                })()
            ''')
            logger.debug("按 Escape 键关闭弹窗")
            time.sleep(0.3)
        except Exception as e:
            logger.warning(f"按 Escape 键失败: {e}")
    
    # =========================================================================
    # 历史记录
    # =========================================================================
    
    def get_popup_history(self) -> List[Dict[str, Any]]:
        """获取弹窗处理历史"""
        return self._popup_history
    
    def clear_popup_history(self) -> None:
        """清空弹窗处理历史"""
        self._popup_history.clear()


# 便捷函数
def handle_popups(session, auto_close: bool = True, timeout: float = 5.0) -> Dict[str, Any]:
    """
    处理页面上的弹窗
    
    Args:
        session: CDP session
        auto_close: 是否自动关闭弹窗
        timeout: 超时时间（秒）
    
    Returns:
        Dict: 处理结果
    """
    handler = PopupHandler(session)
    return handler.handle_popups(auto_close=auto_close, timeout=timeout)
