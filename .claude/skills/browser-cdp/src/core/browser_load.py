"""
browser_load.py - 页面加载与内容抓取统一模块

整合页面导航、等待策略、内容提取，提供统一的抓取接口。
支持多种页面类型：新闻、电商、搜索、社交、学术等。

用法：
  # 加载页面并提取文本
  python browser_load.py --tab <id> --url "https://example.com" --mode text
  
  # 加载页面并提取 HTML
  python browser_load.py --tab <id> --url "https://example.com" --mode html
  
  # 加载页面并提取可交互元素
  python browser_load.py --tab <id> --url "https://example.com" --mode elements
  
  # 加载页面并提取链接
  python browser_load.py --tab <id> --url "https://example.com" --mode links
  
  # 加载页面并提取表单
  python browser_load.py --tab <id> --url "https://example.com" --mode forms
  
  # 加载页面并提取元数据
  python browser_load.py --tab <id> --url "https://example.com" --mode meta
  
  # 保存结果到文件
  python browser_load.py --tab <id> --url "https://example.com" --mode text --save output.txt
  
  # 自定义等待策略
  python browser_load.py --tab <id> --url "https://example.com" --wait-for networkidle --timeout 30
  
  # 启用反检测模式
  python browser_load.py --tab <id> --url "https://example.com" --stealth
  
  # 智能等待（自动选择最优策略）
  python browser_load.py --tab <id> --url "https://example.com" --smart-wait
"""
from __future__ import annotations

import argparse
import json
import time
import logging
from dataclasses import dataclass, field
from typing import Dict, Any, Optional, List
from datetime import datetime

from src.core.utils import add_connection_args, get_session, print_json
from src.core.browser_nav import cmd_goto
from src.core.browser_extract import (
    mode_html,
    TEXT_JS,
    LINKS_JS,
    FORMS_JS,
    META_JS,
    scan_interactive_elements,
)
from src.core.smart_wait import SmartWait
from src.reliability.middleware import (
    get_middleware,
    OperationType,
    with_error_handling,
)
from src.reliability.error import (
    NavigationTimeoutError,
    CDPConnectionLostError,
)

logger = logging.getLogger(__name__)


# ============================================================================
# 页面类型定义
# ============================================================================

PAGE_TYPE_NEWS = "news"
PAGE_TYPE_ECOMMERCE = "ecommerce"
PAGE_TYPE_SEARCH = "search"
PAGE_TYPE_SOCIAL = "social"
PAGE_TYPE_ACADEMIC = "academic"
PAGE_TYPE_GENERAL = "general"

PAGE_TYPES = [
    PAGE_TYPE_NEWS,
    PAGE_TYPE_ECOMMERCE,
    PAGE_TYPE_SEARCH,
    PAGE_TYPE_SOCIAL,
    PAGE_TYPE_ACADEMIC,
    PAGE_TYPE_GENERAL,
]


# ============================================================================
# 抓取结果数据类
# ============================================================================

@dataclass
class LoadResult:
    """页面加载结果"""
    success: bool
    url: str
    title: str
    page_type: str
    mode: str
    data: Dict[str, Any] = field(default_factory=dict)
    error: Optional[str] = None
    elapsed: float = 0.0
    timestamp: str = ""
    
    def to_dict(self) -> dict:
        result = {
            "success": self.success,
            "url": self.url,
            "title": self.title,
            "page_type": self.page_type,
            "mode": self.mode,
            "elapsed": round(self.elapsed, 2),
            "timestamp": self.timestamp,
        }
        if self.error:
            result["error"] = self.error
        if self.data:
            result["data"] = self.data
        return result
    
    def __str__(self) -> str:
        if self.success:
            return f"[ok] 页面加载成功: {self.title} ({self.elapsed:.2f}s)"
        else:
            return f"[error] 页面加载失败: {self.error}"


def format_load_result(
    url: str,
    success: bool,
    title: str = "",
    page_type: str = PAGE_TYPE_GENERAL,
    mode: str = "text",
    data: dict = None,
    error: str = None,
    elapsed: float = 0.0,
) -> LoadResult:
    """格式化页面加载结果"""
    return LoadResult(
        success=success,
        url=url,
        title=title,
        page_type=page_type,
        mode=mode,
        data=data or {},
        error=error,
        elapsed=elapsed,
        timestamp=datetime.now().isoformat(),
    )


# ============================================================================
# 页面类型检测
# ============================================================================

def detect_page_type(url: str, title: str = "", meta: dict = None) -> str:
    """根据 URL、标题和元数据检测页面类型"""
    url_lower = url.lower()
    title_lower = title.lower()
    
    # 新闻站点
    news_keywords = ["news", "新闻", "资讯", "报道", "sina", "netease", "sohu", "toutiao"]
    if any(kw in url_lower or kw in title_lower for kw in news_keywords):
        return PAGE_TYPE_NEWS
    
    # 电商站点
    ecommerce_keywords = ["jd.com", "taobao", "tmall", "pinduoduo", "amazon", "京东", "淘宝", "拼多多"]
    if any(kw in url_lower for kw in ecommerce_keywords):
        return PAGE_TYPE_ECOMMERCE
    
    # 学术站点（优先于搜索检测，避免 scholar.google.com 被误判为搜索）
    academic_keywords = ["arxiv", "scholar", "cnki", "paper", "论文", "学术"]
    if any(kw in url_lower for kw in academic_keywords):
        return PAGE_TYPE_ACADEMIC

    # 搜索站点
    search_keywords = ["baidu", "bing", "google", "search", "搜索"]
    if any(kw in url_lower for kw in search_keywords):
        return PAGE_TYPE_SEARCH

    # 社交站点
    social_keywords = ["zhihu", "weibo", "xiaohongshu", "bilibili", "reddit", "douban", "知乎", "微博", "小红书", "豆瓣"]
    if any(kw in url_lower for kw in social_keywords):
        return PAGE_TYPE_SOCIAL
    
    return PAGE_TYPE_GENERAL


# ============================================================================
# 核心加载函数
# ============================================================================

@with_error_handling("load", OperationType.NAVIGATION, max_retries=3)
def load_page(
    session,
    url: str,
    mode: str = "text",
    wait_for: str = None,
    timeout: float = 30.0,
    smart_wait: bool = True,
    stealth: bool = True,
    max_chars: int = 20000,
) -> LoadResult:
    """
    加载页面并提取内容
    
    Args:
        session: CDP session
        url: 目标 URL
        mode: 提取模式 (html/text/elements/forms/links/meta)
        wait_for: 等待策略 (networkidle/route/stable/selector)
        timeout: 超时时间（秒）
        smart_wait: 是否使用智能等待
        stealth: 是否启用反检测模式
        max_chars: 最大输出字符数
    
    Returns:
        LoadResult: 页面加载结果
    """
    start_time = time.time()
    
    try:
        # 导航到页面
        nav_result = cmd_goto(
            session=session,
            url=url,
            wait_load=True,
            timeout=timeout,
            wait_for=wait_for,
            enable_stealth=stealth,
            smart_wait=smart_wait,
        )
        
        # 获取页面信息
        title = session.eval_js("document.title")
        current_url = session.eval_js("location.href")
        
        # 检测页面类型
        page_type = detect_page_type(current_url, title)
        
        # 根据模式提取内容
        if mode == "html":
            data = mode_html(session)
            out = data[:max_chars]
        elif mode == "text":
            data = session.eval_js(TEXT_JS) or ""
            out = data[:max_chars]
        elif mode == "elements":
            out = scan_interactive_elements(session)
        elif mode == "forms":
            out = session.eval_js(FORMS_JS) or []
        elif mode == "links":
            out = session.eval_js(LINKS_JS) or []
        elif mode == "meta":
            out = session.eval_js(META_JS) or {}
        else:
            out = None
        
        elapsed = time.time() - start_time
        
        result = format_load_result(
            url=current_url,
            success=True,
            title=title,
            page_type=page_type,
            mode=mode,
            data={"content": out},
            elapsed=elapsed,
        )
        
        logger.info(f"页面加载成功: {title} ({page_type}, {mode}, {elapsed:.2f}s)")
        return result
        
    except NavigationTimeoutError as e:
        elapsed = time.time() - start_time
        result = format_load_result(
            url=url,
            success=False,
            error=f"导航超时: {e}",
            elapsed=elapsed,
        )
        logger.error(f"页面加载超时: {e}")
        return result
        
    except CDPConnectionLostError as e:
        elapsed = time.time() - start_time
        result = format_load_result(
            url=url,
            success=False,
            error=f"CDP 连接断开: {e}",
            elapsed=elapsed,
        )
        logger.error(f"CDP 连接断开: {e}")
        return result
        
    except Exception as e:
        elapsed = time.time() - start_time
        result = format_load_result(
            url=url,
            success=False,
            error=str(e),
            elapsed=elapsed,
        )
        logger.error(f"页面加载失败: {e}")
        return result


def main():
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    add_connection_args(parser)
    parser.add_argument(
        "--url",
        required=True,
        help="目标 URL",
    )
    parser.add_argument(
        "--mode",
        choices=["html", "text", "elements", "forms", "links", "meta"],
        default="text",
        help="内容提取模式 (default: text)",
    )
    parser.add_argument(
        "--wait-for",
        choices=["networkidle", "route", "stable", "selector"],
        default=None,
        help="等待策略 (default: 智能等待)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="超时时间（秒）(default: 30)",
    )
    parser.add_argument(
        "--smart-wait",
        action="store_true",
        default=True,
        help="启用智能等待 (default: True)",
    )
    parser.add_argument(
        "--no-smart-wait",
        action="store_false",
        dest="smart_wait",
        help="禁用智能等待",
    )
    parser.add_argument(
        "--stealth",
        action="store_true",
        default=True,
        help="启用反检测模式 (default: True)",
    )
    parser.add_argument(
        "--no-stealth",
        action="store_false",
        dest="stealth",
        help="禁用反检测模式",
    )
    parser.add_argument(
        "--max-chars",
        type=int,
        default=20000,
        help="最大输出字符数 (default: 20000)",
    )
    parser.add_argument(
        "--save",
        default=None,
        help="把结果写入文件而不是打印到 stdout",
    )
    
    args = parser.parse_args()
    session = get_session(args)
    
    try:
        result = load_page(
            session=session,
            url=args.url,
            mode=args.mode,
            wait_for=args.wait_for,
            timeout=args.timeout,
            smart_wait=args.smart_wait,
            stealth=args.stealth,
            max_chars=args.max_chars,
        )
        
        if args.save:
            with open(args.save, "w", encoding="utf-8") as f:
                json.dump(result.to_dict(), f, ensure_ascii=False, indent=2)
            print(f"[ok] 结果已写入: {args.save}")
        else:
            print_json(result.to_dict())
            
        print(f"\n{result}")
        
    finally:
        session.close()


if __name__ == "__main__":
    main()
