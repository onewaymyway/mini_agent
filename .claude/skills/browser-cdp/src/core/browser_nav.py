"""
browser_nav.py - 导航控制（增强版）

用法：
  python browser_nav.py --tab <id> --goto "https://example.com"
  python browser_nav.py --tab <id> --back
  python browser_nav.py --tab <id> --forward
  python browser_nav.py --tab <id> --reload
  python browser_nav.py --tab <id> --wait-selector "#result" --timeout 10
  python browser_nav.py --tab <id> --list                 # 打印当前 tab 的 url/title
  python browser_nav.py --tab <id> --history              # 打印导航历史
  python browser_nav.py --tab <id> --goto "https://example.com" --detect-spa
  python browser_nav.py --tab <id> --goto "https://example.com" --smart-wait
"""
from __future__ import annotations

import argparse
import json
import time
from dataclasses import dataclass, field
from typing import List, Optional, Dict, Any
from datetime import datetime

from src.core.cdp_client import CDPError
from src.core.utils import add_connection_args, get_session, print_json, die
from src.core.smart_wait import SmartWait
from src.core.captcha_handler import CaptchaHandler, CaptchaType, CaptchaResult
from src.core.stealth import StealthMode, StealthConfig
from src.core.spa_detector import SPADetector
from src.reliability.middleware import (
    get_middleware,
    OperationType,
    with_error_handling,
    with_error_handling_async,
)
from src.reliability.error import (
    NavigationTimeoutError,
    CDPConnectionLostError,
    ErrorCategory,
)
from src.reliability.cdp import (
    with_cdp_exception_handling,
    async_with_cdp_exception_handling,
    CDPOperationType,
)

import asyncio
import logging

logger = logging.getLogger(__name__)


@dataclass
class NavigationEntry:
    """导航历史记录条目"""
    url: str
    title: str
    timestamp: str
    type: str = "navigate"  # navigate/back/forward/reload
    duration_ms: float = 0.0
    success: bool = True
    error: Optional[str] = None


class NavigationHistory:
    """导航历史管理器"""
    
    def __init__(self, max_size: int = 50):
        self._history: List[NavigationEntry] = []
        self._max_size = max_size
        self._current_index = -1
    
    def add(self, entry: NavigationEntry):
        """添加导航记录"""
        # 如果是前进操作，截断后面的记录
        if self._current_index < len(self._history) - 1:
            self._history = self._history[:self._current_index + 1]
        
        self._history.append(entry)
        self._current_index = len(self._history) - 1
        
        # 限制历史记录大小
        if len(self._history) > self._max_size:
            self._history.pop(0)
            self._current_index -= 1
    
    def get_history(self, limit: int = 10) -> List[Dict]:
        """获取导航历史"""
        start = max(0, self._current_index - limit + 1)
        return [e.__dict__ for e in self._history[start:self._current_index + 1]]
    
    def get_current(self) -> Optional[NavigationEntry]:
        """获取当前页面记录"""
        if self._current_index >= 0:
            return self._history[self._current_index]
        return None
    
    def clear(self):
        """清空历史"""
        self._history.clear()
        self._current_index = -1


# 全局导航历史（每个 tab 一个）
_navigation_histories: Dict[str, NavigationHistory] = {}


def get_nav_history(tab_id: str) -> NavigationHistory:
    """获取指定 tab 的导航历史"""
    if tab_id not in _navigation_histories:
        _navigation_histories[tab_id] = NavigationHistory()
    return _navigation_histories[tab_id]


@with_cdp_exception_handling("navigate", CDPOperationType.NAVIGATE, max_retries=3, target_url="{url}")
@with_error_handling_async("navigate", OperationType.NAVIGATION, max_retries=3)
async def async_cmd_goto(session, url: str, wait_load: bool, timeout: float, 
                         wait_for: str = None, enable_stealth: bool = True,  # 默认启用反检测
                         handle_captcha: bool = False, detect_spa: bool = False,
                         smart_wait: bool = True, tab_id: str = None):  # 默认启用智能等待
    """
    异步导航命令，支持反检测、验证码处理、SPA 检测

    Args:
        session: CDP session
        url: 目标 URL
        wait_load: 是否等待页面加载
        timeout: 超时时间（秒）
        wait_for: 等待策略（networkidle/route/stable/selector）
        enable_stealth: 是否启用反检测模式
        handle_captcha: 是否自动处理验证码
        detect_spa: 是否检测 SPA 路由
        smart_wait: 是否使用智能等待（自动选择最优策略）
        tab_id: Tab ID（用于记录导航历史）
    """
    start_time = time.time()
    nav_entry = NavigationEntry(
        url=url,
        title="",
        timestamp=datetime.now().isoformat(),
        type="navigate"
    )

    try:
        # 应用反检测模式
        if enable_stealth:
            stealth = StealthMode(session, StealthConfig())
            await stealth.apply()
            logger.info("已启用反检测模式")

        # 记录当前 URL
        old_url = session.eval_js("location.href")

        # 导航到目标 URL
        session.send("Page.navigate", {"url": url})

        # 智能等待策略
        if smart_wait:
            # 自动检测页面类型并选择最优等待策略
            smart_wait_handler = SmartWait(session)
            wait_strategy = await smart_wait_handler.wait_for("adaptive", timeout=timeout)
            logger.info(f"智能等待策略: {wait_strategy}")
        elif wait_for:
            smart_wait_handler = SmartWait(session)
            await smart_wait_handler.wait_for(wait_for, idle_timeout=timeout)
        elif wait_load:
            try:
                session.wait_event("Page.loadEventFired", timeout=timeout)
            except CDPError:
                print("[warn] 等待 load 事件超时，页面可能仍在加载或使用了长轮询/SPA 路由")
                # 降级：尝试 networkidle 等待
                try:
                    smart_wait_handler = SmartWait(session)
                    await smart_wait_handler.wait_for("networkidle", idle_timeout=timeout)
                except Exception:
                    pass

        # SPA 路由检测
        if detect_spa:
            spa_detector = SPADetector(session)
            is_spa = await spa_detector.detect()
            if is_spa:
                logger.info("检测到 SPA 路由，等待路由稳定")
                smart_wait_handler = SmartWait(session)
                await smart_wait_handler.wait_for("route", idle_timeout=timeout)

        # 获取页面信息
        new_url = session.eval_js("location.href")
        title = session.eval_js("document.title")
        nav_entry.title = title
        nav_entry.success = True

        # 检测并处理验证码
        if handle_captcha:
            captcha_handler = CaptchaHandler(session)
            captcha_result = await captcha_handler.handle_captcha()
            if not captcha_result.success:
                logger.warning(f"验证码处理失败: {captcha_result.message}")
                print(f"[warn] 检测到 {captcha_result.captcha_type.value} 验证码，需要手动处理")
                print(f"[info] 提示: {captcha_result.message}")
                nav_entry.error = captcha_result.message
            else:
                logger.info(f"验证码处理成功: {captcha_result}")
                print(f"[ok] 验证码已处理: {captcha_result}")

        # 记录导航历史
        nav_entry.duration_ms = (time.time() - start_time) * 1000
        if tab_id:
            history = get_nav_history(tab_id)
            history.add(nav_entry)

        # 输出导航结果
        result = {
            "url": new_url,
            "title": title,
            "duration_ms": nav_entry.duration_ms,
            "success": nav_entry.success,
            "spa_detected": detect_spa and await spa_detector.detect() if detect_spa else False
        }
        print_json(result)

    except NavigationTimeoutError as e:
        # 可恢复错误，重新抛出供中间件处理
        nav_entry.success = False
        nav_entry.error = str(e)
        nav_entry.duration_ms = (time.time() - start_time) * 1000
        logger.error(f"导航超时: {e}")
        if tab_id:
            history = get_nav_history(tab_id)
            history.add(nav_entry)
        raise
    except CDPConnectionLostError as e:
        # CDP 连接断开，可恢复
        nav_entry.success = False
        nav_entry.error = str(e)
        nav_entry.duration_ms = (time.time() - start_time) * 1000
        logger.error(f"CDP 连接断开: {e}")
        if tab_id:
            history = get_nav_history(tab_id)
            history.add(nav_entry)
        raise
    except Exception as e:
        nav_entry.success = False
        nav_entry.error = str(e)
        nav_entry.duration_ms = (time.time() - start_time) * 1000
        logger.error(f"导航失败: {e}")
        print(f"[error] 导航失败: {e}")
        if tab_id:
            history = get_nav_history(tab_id)
            history.add(nav_entry)
        raise


def cmd_goto(session, url: str, wait_load: bool, timeout: float, 
             wait_for: str = None, enable_stealth: bool = True,  # 默认启用反检测
             handle_captcha: bool = False, detect_spa: bool = False,
             smart_wait: bool = True, tab_id: str = None):  # 默认启用智能等待
    """
    导航命令（同步版本，内部调用异步版本）
    """
    if enable_stealth or handle_captcha or detect_spa or smart_wait:
        # 需要异步执行
        asyncio.run(async_cmd_goto(session, url, wait_load, timeout, wait_for, 
                                   enable_stealth, handle_captcha, detect_spa, 
                                   smart_wait, tab_id))
    else:
        # 原有逻辑（同步版本）
        session.send("Page.navigate", {"url": url})
        if wait_for:
            smart_wait_handler = SmartWait(session)
            asyncio.run(smart_wait_handler.wait_for(wait_for, idle_timeout=timeout))
        elif wait_load:
            try:
                session.wait_event("Page.loadEventFired", timeout=timeout)
            except CDPError:
                print("[warn] 等待 load 事件超时，页面可能仍在加载或使用了长轮询/SPA 路由")
                # 降级：尝试 networkidle 等待
                try:
                    smart_wait_handler = SmartWait(session)
                    asyncio.run(smart_wait_handler.wait_for("networkidle", idle_timeout=timeout))
                except Exception:
                    pass
        
        # 记录导航历史
        new_url = session.eval_js("location.href")
        title = session.eval_js("document.title")
        nav_entry = NavigationEntry(
            url=url,
            title=title,
            timestamp=datetime.now().isoformat(),
            type="navigate",
            duration_ms=0,
            success=True
        )
        if tab_id:
            history = get_nav_history(tab_id)
            history.add(nav_entry)


@with_cdp_exception_handling("wait_selector", CDPOperationType.QUERY_SELECTOR, selector="{selector}", max_retries=2)
def cmd_wait_selector(session, selector: str, timeout: float):
    deadline = time.time() + timeout
    js = f"!!document.querySelector({selector!r})"
    while time.time() < deadline:
        try:
            if session.eval_js(js):
                print(f"[ok] 元素已出现: {selector}")
                return
        except Exception:
            pass
        time.sleep(0.3)
    die(f"等待元素超时: {selector}")


@with_cdp_exception_handling("get_url", CDPOperationType.EVAL_JS)
def get_url(session) -> str:
    """获取当前页面 URL"""
    return session.eval_js("location.href")


@with_cdp_exception_handling("wait_element", CDPOperationType.QUERY_SELECTOR, selector="{selector}", max_retries=2)
def wait_element(session, selector: str, timeout: float = 10.0) -> bool:
    """等待元素出现（返回 bool，不抛异常）"""
    deadline = time.time() + timeout
    js = f"!!document.querySelector({selector!r})"
    while time.time() < deadline:
        try:
            if session.eval_js(js):
                return True
        except Exception:
            pass
        time.sleep(0.3)
    return False


@with_cdp_exception_handling("wait_element_not_present", CDPOperationType.QUERY_SELECTOR, selector="{selector}", max_retries=2)
def wait_element_not_present(session, selector: str, timeout: float = 10.0) -> bool:
    """等待元素消失（返回 bool，不抛异常）"""
    deadline = time.time() + timeout
    js = f"!!document.querySelector({selector!r})"
    while time.time() < deadline:
        try:
            if not session.eval_js(js):
                return True
        except Exception:
            return True
        time.sleep(0.3)
    return False


@with_cdp_exception_handling("current_state", CDPOperationType.EVAL_JS)
def current_state(session) -> dict:
    url = session.eval_js("location.href")
    title = session.eval_js("document.title")
    ready = session.eval_js("document.readyState")
    return {"url": url, "title": title, "readyState": ready}


def cmd_history(session, tab_id: str, limit: int = 10):
    """打印导航历史"""
    history = get_nav_history(tab_id)
    entries = history.get_history(limit)
    print_json({"history": entries, "current_index": history._current_index})


def cmd_clear_history(tab_id: str):
    """清空导航历史"""
    if tab_id in _navigation_histories:
        _navigation_histories[tab_id].clear()
        print("[ok] 导航历史已清空")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_connection_args(parser)
    parser.add_argument("--goto", metavar="URL", default=None)
    parser.add_argument("--back", action="store_true")
    parser.add_argument("--forward", action="store_true")
    parser.add_argument("--reload", action="store_true")
    parser.add_argument("--wait-selector", default=None, help="等待某个 CSS 选择器对应元素出现")
    parser.add_argument("--wait-for", default=None, choices=["load", "networkidle", "route", "stable", "ajax", "selector"], help="智能等待策略")
    parser.add_argument("--no-wait-load", action="store_true", help="goto 后不等待 load 事件，立即返回")
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--stealth", action="store_true", help="启用反检测模式（隐藏自动化特征）")
    parser.add_argument("--handle-captcha", action="store_true", help="自动检测并处理验证码")
    parser.add_argument("--user-agent", default=None, help="自定义 User-Agent")
    parser.add_argument("--detect-spa", action="store_true", help="检测 SPA 路由并等待稳定")
    parser.add_argument("--smart-wait", action="store_true", help="使用智能等待策略（自动选择最优策略）")
    parser.add_argument("--history", action="store_true", help="打印导航历史")
    parser.add_argument("--clear-history", action="store_true", help="清空导航历史")
    parser.add_argument("--history-limit", type=int, default=10, help="导航历史显示条数")

    args = parser.parse_args()
    session = get_session(args)
    
    # 获取 tab_id
    tab_id = getattr(args, 'tab', None)
    
    try:
        if args.goto:
            wait_for = args.wait_for if args.wait_for else ("networkidle" if not args.no_wait_load else None)
            cmd_goto(
                session, 
                args.goto, 
                wait_load=not args.no_wait_load, 
                timeout=args.timeout, 
                wait_for=wait_for,
                enable_stealth=args.stealth,
                handle_captcha=args.handle_captcha,
                detect_spa=args.detect_spa,
                smart_wait=args.smart_wait,
                tab_id=tab_id
            )
        if args.back:
            session.eval_js("history.back()")
        if args.forward:
            session.eval_js("history.forward()")
        if args.reload:
            session.send("Page.reload")
            if not args.no_wait_load:
                try:
                    session.wait_event("Page.loadEventFired", timeout=args.timeout)
                except CDPError:
                    pass
        if args.wait_selector:
            cmd_wait_selector(session, args.wait_selector, args.timeout)
        if args.history:
            cmd_history(session, tab_id, args.history_limit)
        if args.clear_history:
            cmd_clear_history(tab_id)

        print_json(current_state(session))
    finally:
        session.close()


if __name__ == "__main__":
    main()
