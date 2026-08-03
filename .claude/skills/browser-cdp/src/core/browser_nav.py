"""
browser_nav.py - 导航控制

用法：
  python browser_nav.py --tab <id> --goto "https://example.com"
  python browser_nav.py --tab <id> --back
  python browser_nav.py --tab <id> --forward
  python browser_nav.py --tab <id> --reload
  python browser_nav.py --tab <id> --wait-selector "#result" --timeout 10
  python browser_nav.py --list                 # 不带 --goto 等操作时，打印当前 tab 的 url/title
"""
from __future__ import annotations

import argparse
import time

from src.core.cdp_client import CDPError
from src.core.utils import add_connection_args, get_session, print_json, die
from src.core.smart_wait import SmartWait
from src.core.captcha_handler import CaptchaHandler, CaptchaType, CaptchaResult
from src.core.stealth import StealthMode, StealthConfig

import asyncio
import logging

logger = logging.getLogger(__name__)


async def async_cmd_goto(session, url: str, wait_load: bool, timeout: float, wait_for: str = None, 
                         enable_stealth: bool = False, handle_captcha: bool = False):
    """
    异步导航命令，支持反检测和验证码处理
    
    Args:
        session: CDP session
        url: 目标 URL
        wait_load: 是否等待页面加载
        timeout: 超时时间（秒）
        wait_for: 等待策略（networkidle/route/stable/selector）
        enable_stealth: 是否启用反检测模式
        handle_captcha: 是否自动处理验证码
    """
    # 应用反检测模式
    if enable_stealth:
        stealth = StealthMode(session, StealthConfig())
        await stealth.apply()
        logger.info("已启用反检测模式")
    
    # 导航到目标 URL
    session.send("Page.navigate", {"url": url})
    
    # 智能等待
    if wait_for:
        smart_wait = SmartWait(session)
        await smart_wait.wait_for(wait_for, timeout=timeout)
    elif wait_load:
        try:
            session.wait_event("Page.loadEventFired", timeout=timeout)
        except CDPError:
            print("[warn] 等待 load 事件超时，页面可能仍在加载或使用了长轮询/SPA 路由")
            # 降级：尝试 networkidle 等待
            try:
                smart_wait = SmartWait(session)
                await smart_wait.wait_for("networkidle", timeout=timeout)
            except Exception:
                pass
    
    # 检测并处理验证码
    if handle_captcha:
        captcha_handler = CaptchaHandler(session)
        captcha_result = await captcha_handler.handle_captcha()
        if not captcha_result.success:
            logger.warning(f"验证码处理失败: {captcha_result.message}")
            print(f"[warn] 检测到 {captcha_result.captcha_type.value} 验证码，需要手动处理")
            print(f"[info] 提示: {captcha_result.message}")
        else:
            logger.info(f"验证码处理成功: {captcha_result}")
            print(f"[ok] 验证码已处理: {captcha_result}")


def cmd_goto(session, url: str, wait_load: bool, timeout: float, wait_for: str = None,
             enable_stealth: bool = False, handle_captcha: bool = False):
    """
    导航命令（同步版本，内部调用异步版本）
    """
    if enable_stealth or handle_captcha:
        # 需要异步执行
        asyncio.run(async_cmd_goto(session, url, wait_load, timeout, wait_for, 
                                   enable_stealth, handle_captcha))
    else:
        # 原有逻辑
        session.send("Page.navigate", {"url": url})
        if wait_for:
            smart_wait = SmartWait(session)
            asyncio.run(smart_wait.wait_for(wait_for, timeout=timeout))
        elif wait_load:
            try:
                session.wait_event("Page.loadEventFired", timeout=timeout)
            except CDPError:
                print("[warn] 等待 load 事件超时，页面可能仍在加载或使用了长轮询/SPA 路由")
                # 降级：尝试 networkidle 等待
                try:
                    smart_wait = SmartWait(session)
                    asyncio.run(smart_wait.wait_for("networkidle", timeout=timeout))
                except Exception:
                    pass


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


def current_state(session) -> dict:
    url = session.eval_js("location.href")
    title = session.eval_js("document.title")
    ready = session.eval_js("document.readyState")
    return {"url": url, "title": title, "readyState": ready}


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

    args = parser.parse_args()
    session = get_session(args)
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
                handle_captcha=args.handle_captcha
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

        print_json(current_state(session))
    finally:
        session.close()


if __name__ == "__main__":
    main()
