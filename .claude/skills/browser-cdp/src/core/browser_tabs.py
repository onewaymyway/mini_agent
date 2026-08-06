"""
browser_tabs.py - 多标签页管理

支持：
- 标签页列表和状态查询
- 标签页切换
- 批量操作（批量导航、批量截图、批量提取）
- 标签页组管理
- 标签页清理

用法：
  python browser_tabs.py --port 9333 --list
  python browser_tabs.py --port 9333 --new "https://example.com"
  python browser_tabs.py --port 9333 --activate <target_id>
  python browser_tabs.py --port 9333 --close <target_id>
  python browser_tabs.py --port 9333 --close-all --keep 3
  python browser_tabs.py --port 9333 --batch-goto --urls url1,url2,url3
  python browser_tabs.py --port 9333 --batch-screenshot --out-dir ./screenshots
  python browser_tabs.py --port 9333 --batch-extract --mode text --out-dir ./extracted
"""
from __future__ import annotations

import argparse
import json
import os
import time
from typing import Any

from src.core.cdp_client import (
    list_tabs,
    new_tab,
    close_tab,
    activate_tab,
    is_debug_port_alive,
)
from src.core.utils import print_json, die
from src.reliability.middleware import (
    get_middleware,
    OperationType,
    with_error_handling,
)


# 标签页组定义格式：
# {
#   "name": "research",
#   "tabs": [
#     {"url": "https://example1.com", "title": "Page 1"},
#     {"url": "https://example2.com", "title": "Page 2"}
#   ]
# }


@with_error_handling("get_tab_info", OperationType.TAB, max_retries=2)
def get_tab_info(session) -> dict:
    """获取当前标签页信息。"""
    js = """(() => {
        return {
            url: location.href,
            title: document.title,
            readyState: document.readyState,
            visibilityState: document.visibilityState,
            performance: {
                navigationStart: performance.timing.navigationStart,
                loadEventEnd: performance.timing.loadEventEnd,
                domContentLoaded: performance.timing.domContentLoadedEventEnd
            }
        };
    })()"""
    
    try:
        info = session.eval_js(js)
        info["last_updated"] = time.time()
        return info
    except Exception as e:
        return {"error": str(e)}


def list_tabs_info(host: str = "127.0.0.1", port: int = 9222) -> list[dict]:
    """列出所有标签页及其状态。"""
    tabs = list_tabs(host, port)
    result = []
    
    for tab in tabs:
        target_id = tab.get("id")
        ws_url = tab.get("webSocketDebuggerUrl")
        
        # 获取标签页基本信息
        info = {
            "target_id": target_id,
            "url": tab.get("url", ""),
            "title": tab.get("title", ""),
            "type": tab.get("type", "page"),
            "ws_url": ws_url,
        }
        
        # 尝试获取更详细的信息
        try:
            from src.core.cdp_client import CDPSession
            session = CDPSession(ws_url)
            tab_info = get_tab_info(session)
            session.close()
            info.update(tab_info)
        except Exception:
            pass
        
        result.append(info)
    
    return result


@with_error_handling("create_tab", OperationType.NAVIGATION, max_retries=3)
def create_tab(url: str = "about:blank", host: str = "127.0.0.1", port: int = 9222) -> dict:
    """创建新标签页。"""
    tab = new_tab(url, host, port)
    return {
        "target_id": tab.get("id"),
        "url": tab.get("url"),
        "title": tab.get("title"),
        "ws_url": tab.get("webSocketDebuggerUrl"),
        "created_at": time.time(),
    }


@with_error_handling("switch_tab", OperationType.NAVIGATION, max_retries=3)
def switch_tab(target_id: str, host: str = "127.0.0.1", port: int = 9222) -> dict:
    """切换到指定标签页。"""
    activate_tab(target_id, host, port)
    return {"switched_to": target_id, "timestamp": time.time()}


@with_error_handling("close_tab", OperationType.NAVIGATION, max_retries=2)
def close_tab_by_id(target_id: str, host: str = "127.0.0.1", port: int = 9222) -> bool:
    """关闭指定标签页。"""
    try:
        close_tab(target_id, host, port)
        return True
    except Exception as e:
        print(f"[error] 关闭标签页失败：{e}")
        return False


def close_all_tabs(host: str = "127.0.0.1", port: int = 9222, keep: int = 0) -> list[dict]:
    """关闭所有标签页，保留指定数量。"""
    tabs = list_tabs(host, port)
    closed = []
    
    # 按创建时间排序，保留最新的 keep 个
    tabs_sorted = sorted(tabs, key=lambda t: t.get("attached", 0), reverse=True)
    to_close = tabs_sorted[keep:]
    
    for tab in to_close:
        target_id = tab.get("id")
        if close_tab_by_id(target_id, host, port):
            closed.append({
                "target_id": target_id,
                "url": tab.get("url"),
                "title": tab.get("title"),
            })
    
    return closed


def batch_goto(urls: list[str], host: str = "127.0.0.1", port: int = 9222) -> list[dict]:
    """批量导航到多个 URL（每个 URL 一个标签页）。"""
    results = []
    
    for i, url in enumerate(urls):
        tab = create_tab(url, host, port)
        results.append({
            "index": i,
            "url": url,
            "target_id": tab["target_id"],
            "status": "created",
        })
        time.sleep(0.5)  # 避免过快创建
    
    return results


def batch_screenshot(
    host: str = "127.0.0.1", 
    port: int = 9222, 
    out_dir: str = "./screenshots",
    full_page: bool = False,
) -> list[dict]:
    """批量截图所有标签页。"""
    os.makedirs(out_dir, exist_ok=True)
    tabs = list_tabs(host, port)
    results = []
    
    for tab in tabs:
        target_id = tab.get("id")
        ws_url = tab.get("webSocketDebuggerUrl")
        
        try:
            from src.core.cdp_client import CDPSession
            session = CDPSession(ws_url)
            
            # 截图
            from src.core.browser_screenshot import capture_screenshot
            screenshot_path = capture_screenshot(
                session,
                out_path=os.path.join(out_dir, f"tab_{target_id[:8]}.png"),
                full_page=full_page,
            )
            
            results.append({
                "target_id": target_id,
                "url": tab.get("url"),
                "screenshot": screenshot_path,
                "status": "ok",
            })
            
            session.close()
        except Exception as e:
            results.append({
                "target_id": target_id,
                "url": tab.get("url"),
                "error": str(e),
                "status": "error",
            })
    
    return results


def batch_extract(
    host: str = "127.0.0.1", 
    port: int = 9222, 
    mode: str = "text",
    out_dir: str = "./extracted",
) -> list[dict]:
    """批量提取所有标签页内容。"""
    os.makedirs(out_dir, exist_ok=True)
    tabs = list_tabs(host, port)
    results = []
    
    for tab in tabs:
        target_id = tab.get("id")
        ws_url = tab.get("webSocketDebuggerUrl")
        
        try:
            from src.core.cdp_client import CDPSession
            session = CDPSession(ws_url)
            
            # 提取内容
            from src.core.browser_extract import extract_content
            content = extract_content(session, mode=mode)
            
            # 保存到文件
            out_path = os.path.join(out_dir, f"tab_{target_id[:8]}.txt")
            with open(out_path, 'w', encoding='utf-8') as f:
                f.write(content)
            
            results.append({
                "target_id": target_id,
                "url": tab.get("url"),
                "content_path": out_path,
                "status": "ok",
            })
            
            session.close()
        except Exception as e:
            results.append({
                "target_id": target_id,
                "url": tab.get("url"),
                "error": str(e),
                "status": "error",
            })
    
    return results


def create_tab_group(name: str, tabs: list[dict], host: str = "127.0.0.1", port: int = 9222) -> dict:
    """创建标签页组。"""
    group = {
        "name": name,
        "tabs": [],
        "created_at": time.time(),
    }
    
    for tab_def in tabs:
        url = tab_def.get("url", "about:blank")
        tab = create_tab(url, host, port)
        group["tabs"].append({
            "target_id": tab["target_id"],
            "url": url,
            "title": tab_def.get("title", ""),
        })
        time.sleep(0.3)
    
    return group


def switch_to_group(group: dict, host: str = "127.0.0.1", port: int = 9222) -> dict:
    """切换到标签页组中的第一个标签页。"""
    if not group.get("tabs"):
        return {"error": "标签页组为空"}
    
    first_tab = group["tabs"][0]
    switch_tab(first_tab["target_id"], host, port)
    return {
        "switched_to": first_tab["target_id"],
        "group": group["name"],
    }


def close_group(group: dict, host: str = "127.0.0.1", port: int = 9222) -> list[dict]:
    """关闭标签页组中的所有标签页。"""
    closed = []
    for tab in group.get("tabs", []):
        if close_tab_by_id(tab["target_id"], host, port):
            closed.append(tab)
    return closed


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--port", type=int, default=9222, help="调试端口")
    parser.add_argument("--host", default="127.0.0.1", help="主机地址")
    
    # 标签页操作
    parser.add_argument("--list", action="store_true", help="列出所有标签页")
    parser.add_argument("--new", metavar="URL", help="创建新标签页")
    parser.add_argument("--activate", metavar="TARGET_ID", help="切换到指定标签页")
    parser.add_argument("--close", metavar="TARGET_ID", help="关闭指定标签页")
    parser.add_argument("--close-all", action="store_true", help="关闭所有标签页")
    parser.add_argument("--keep", type=int, default=0, help="关闭所有时保留的数量")
    
    # 批量操作
    parser.add_argument("--batch-goto", metavar="URLS", help="批量导航（逗号分隔的 URL）")
    parser.add_argument("--batch-screenshot", action="store_true", help="批量截图")
    parser.add_argument("--batch-extract", action="store_true", help="批量提取内容")
    parser.add_argument("--out-dir", default="./output", help="输出目录")
    parser.add_argument("--full-page", action="store_true", help="整页截图")
    parser.add_argument("--mode", default="text", choices=["html", "text", "elements", "forms", "links", "meta"], help="提取模式")
    
    # 标签页组
    parser.add_argument("--create-group", metavar="NAME", help="创建标签页组")
    parser.add_argument("--group-tabs", metavar="JSON", help="标签页组定义（JSON）")
    parser.add_argument("--switch-group", metavar="NAME", help="切换到标签页组")
    parser.add_argument("--close-group", metavar="NAME", help="关闭标签页组")
    
    args = parser.parse_args()
    
    try:
        if args.list:
            tabs = list_tabs_info(args.host, args.port)
            print_json(tabs)
        
        elif args.new:
            tab = create_tab(args.new, args.host, args.port)
            print_json(tab)
        
        elif args.activate:
            result = switch_tab(args.activate, args.host, args.port)
            print_json(result)
        
        elif args.close:
            success = close_tab_by_id(args.close, args.host, args.port)
            print_json({"closed": args.close, "success": success})
        
        elif args.close_all:
            closed = close_all_tabs(args.host, args.port, keep=args.keep)
            print_json({"closed_count": len(closed), "tabs": closed})
        
        elif args.batch_goto:
            urls = [u.strip() for u in args.batch_goto.split(",")]
            results = batch_goto(urls, args.host, args.port)
            print_json(results)
        
        elif args.batch_screenshot:
            results = batch_screenshot(args.host, args.port, args.out_dir, args.full_page)
            print_json(results)
        
        elif args.batch_extract:
            results = batch_extract(args.host, args.port, args.mode, args.out_dir)
            print_json(results)
        
        elif args.create_group:
            if not args.group_tabs:
                die("--create-group 需要配合 --group-tabs 使用")
            tabs_def = json.loads(args.group_tabs)
            group = create_tab_group(args.create_group, tabs_def, args.host, args.port)
            print_json(group)
        
        elif args.switch_group:
            # 从文件加载组定义
            die("--switch-group 需要从文件加载组定义，请使用 --group-tabs 参数")
        
        elif args.close_group:
            die("--close-group 需要从文件加载组定义，请使用 --group-tabs 参数")
        
        else:
            parser.print_help()
    
    except Exception as e:
        die(f"操作失败：{e}")


if __name__ == "__main__":
    main()
