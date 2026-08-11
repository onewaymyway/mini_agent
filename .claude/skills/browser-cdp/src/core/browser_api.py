"""
browser_api.py - 统一 API 封装层

整合 DOM 内容抓取（browser_extract）和截图能力（browser_screenshot），
提供标准化的输入输出格式，方便 Agent 调用。

输入格式（JSON Schema）：
  {
    "action": "extract" | "screenshot",
    "params": { ... }
  }

输出格式（JSON）：
  {
    "success": true,
    "action": "extract" | "screenshot",
    "result": { ... },
    "error": null
  }
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, asdict
from typing import Any, Optional

from src.core.browser_extract import (
    mode_html,
    mode_text,
    mode_links,
    mode_forms,
    mode_meta,
    extract_elements,
    extract_xpath,
    extract_text,
)
from src.core.browser_screenshot import (
    capture,
    annotate_png,
    save_screenshot,
    smart_region_crop,
    zoom_screenshot,
    compare_screenshots,
)
from src.core.utils import get_session, scan_interactive_elements, print_json
from src.reliability.middleware import with_error_handling, OperationType


# ============================================================================
# 输入/输出 Schema 定义
# ============================================================================

EXTRACT_ACTIONS = ["html", "text", "elements", "forms", "links", "meta", "xpath"]
SCREENSHOT_ACTIONS = ["viewport", "fullpage", "region", "element", "annotate", "compare", "zoom"]


@dataclass
class ApiResult:
    """统一 API 结果"""
    success: bool
    action: str
    result: dict = None
    error: Optional[str] = None
    elapsed: float = 0.0
    metadata: dict = None

    def to_dict(self) -> dict:
        d = {
            "success": self.success,
            "action": self.action,
            "elapsed": round(self.elapsed, 3),
        }
        if self.error:
            d["error"] = self.error
        if self.result is not None:
            d["result"] = self.result
        if self.metadata:
            d["metadata"] = self.metadata
        return d

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)


# ============================================================================
# 内容抓取 API
# ============================================================================

class ExtractAPI:
    """DOM 内容抓取统一 API"""

    @staticmethod
    @with_error_handling("extract", OperationType.EXTRACT, max_retries=2)
    def extract(session, action: str, params: dict = None) -> ApiResult:
        """
        统一内容抓取入口

        Args:
            session: CDP session
            action: 抓取模式 (html/text/elements/forms/links/meta/xpath)
            params: 额外参数
                - selector: CSS 选择器或 XPath 表达式
                - xpath: 是否使用 XPath
                - max_chars: 最大输出长度
                - save: 保存路径

        Returns:
            ApiResult
        """
        params = params or {}
        start = time.time()
        selector = params.get("selector")
        xpath = params.get("xpath", False)
        max_chars = params.get("max_chars", 20000)
        save_path = params.get("save")

        try:
            if action == "html":
                data = mode_html(session)
                data = data[:max_chars]
            elif action == "text":
                if selector:
                    data = extract_text(session, selector, xpath=xpath)
                else:
                    data = mode_text(session)
                data = data[:max_chars]
            elif action == "elements":
                if selector:
                    data = extract_elements(session, selector, xpath=xpath)
                else:
                    data = scan_interactive_elements(session)
            elif action == "forms":
                data = mode_forms(session)
            elif action == "links":
                data = mode_links(session)
            elif action == "meta":
                data = mode_meta(session)
            elif action == "xpath":
                if not selector:
                    raise ValueError("--mode xpath 需要指定 --selector")
                data = extract_xpath(session, selector)
            else:
                raise ValueError(f"未知抓取模式: {action}")

            # 保存结果
            if save_path:
                with open(save_path, "w", encoding="utf-8") as f:
                    if isinstance(data, str):
                        f.write(data)
                    else:
                        json.dump(data, f, ensure_ascii=False, indent=2)

            elapsed = time.time() - start
            return ApiResult(
                success=True,
                action=f"extract:{action}",
                result={"data": data, "saved": save_path},
                elapsed=elapsed,
                metadata={"mode": action, "count": len(data) if isinstance(data, (str, list)) else 0},
            )

        except Exception as e:
            elapsed = time.time() - start
            return ApiResult(
                success=False,
                action=f"extract:{action}",
                error=str(e),
                elapsed=elapsed,
            )


# ============================================================================
# 截图 API
# ============================================================================

class ScreenshotAPI:
    """截图统一 API"""

    @staticmethod
    @with_error_handling("screenshot", OperationType.SCREENSHOT, max_retries=2)
    def screenshot(session, action: str, params: dict = None) -> ApiResult:
        """
        统一截图入口

        Args:
            session: CDP session
            action: 截图模式
                - viewport: 可视区域截图（默认）
                - fullpage: 整页截图
                - region: 智能区域截图 (nav/main/sidebar/content/footer)
                - element: 元素截图（需 element_index）
                - annotate: 标注截图
                - compare: 对比截图（需 compare_path）
                - zoom: 缩放截图（需 zoom_factor）
            params: 额外参数
                - out: 输出路径
                - element_index: 元素编号
                - region: 区域名称
                - compare_path: 对比截图路径
                - zoom_factor: 缩放倍数
                - detail: 详细标注
                - no_scroll: 不标注滚动外元素
                - fmt: 输出格式 (png/jpeg)
                - quality: JPEG 质量

        Returns:
            ApiResult
        """
        params = params or {}
        start = time.time()

        out = params.get("out", f"screenshot_{int(time.time())}.png")
        full_page = action == "fullpage"
        annotate = action in ("annotate",)
        element_index = params.get("element_index")
        region = params.get("region")
        compare_path = params.get("compare_path")
        zoom_factor = params.get("zoom_factor", 1.0)
        detail = params.get("detail", False)
        no_scroll = params.get("no_scroll", False)
        fmt = params.get("fmt", "png")
        quality = params.get("quality", 95)

        try:
            # 元素标注需要扫描
            elements = []
            if annotate or element_index is not None:
                elements = scan_interactive_elements(session)

            # 元素截图
            clip = None
            if element_index is not None:
                target = next((e for e in elements if e["index"] == element_index), None)
                if not target:
                    raise ValueError(f"未找到编号为 {element_index} 的元素")
                r = target["rect"]
                clip = {"x": r["x"], "y": r["y"], "width": r["width"], "height": r["height"], "scale": 1}

            # 截图
            png_bytes = capture(session, full_page=full_page, clip=clip)

            # 区域裁剪
            if region:
                png_bytes = smart_region_crop(png_bytes, region)

            # 缩放
            if zoom_factor > 1.0:
                png_bytes = zoom_screenshot(png_bytes, zoom_factor)

            # 标注
            if annotate and clip is None:
                png_bytes = annotate_png(png_bytes, elements, detail=detail, no_scroll=no_scroll)

            # 对比
            if compare_path:
                with open(compare_path, "rb") as f:
                    other_bytes = f.read()
                png_bytes = compare_screenshots(png_bytes, other_bytes)

            # 保存
            save_screenshot(png_bytes, out, fmt=fmt, quality=quality)

            elapsed = time.time() - start
            result = {"path": out, "size_bytes": os.path.getsize(out)}
            if annotate:
                side_path = os.path.splitext(out)[0] + ".elements.json"
                with open(side_path, "w", encoding="utf-8") as f:
                    json.dump(elements, f, ensure_ascii=False, indent=2)
                result["elements_file"] = side_path
                result["element_count"] = len(elements)

            return ApiResult(
                success=True,
                action=f"screenshot:{action}",
                result=result,
                elapsed=elapsed,
                metadata={"format": fmt, "size": result.get("size_bytes", 0)},
            )

        except Exception as e:
            elapsed = time.time() - start
            return ApiResult(
                success=False,
                action=f"screenshot:{action}",
                error=str(e),
                elapsed=elapsed,
            )


# ============================================================================
# 统一入口
# ============================================================================

def browser_api(tab_id: str, action: str, params: dict = None) -> ApiResult:
    """
    统一浏览器 API 入口

    Args:
        tab_id: 标签页 ID
        action: 操作类型 (extract/screenshot)
        params: 操作参数

    Returns:
        ApiResult

    Examples:
        # 抓取页面文本
        browser_api("1", "extract", {"action": "text"})

        # 截图并标注
        browser_api("1", "screenshot", {"action": "annotate", "out": "shot.png"})
    """
    params = params or {}
    start = time.time()

    # 获取 session
    try:
        session = get_session(type('Args', (), {'tab_id': tab_id, 'cdp_port': 9222})())
    except Exception as e:
        return ApiResult(
            success=False,
            action=action,
            error=f"无法连接浏览器: {e}",
            elapsed=time.time() - start,
        )

    try:
        if action == "extract":
            extract_action = params.get("action", "text")
            return ExtractAPI.extract(session, extract_action, params)
        elif action == "screenshot":
            screenshot_action = params.get("action", "viewport")
            return ScreenshotAPI.screenshot(session, screenshot_action, params)
        else:
            return ApiResult(
                success=False,
                action=action,
                error=f"未知操作类型: {action}，可选: extract, screenshot",
                elapsed=time.time() - start,
            )
    finally:
        session.close()


# ============================================================================
# CLI 入口
# ============================================================================

def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Browser API - 统一浏览器操作接口",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 抓取页面文本
  python browser_api.py --tab 1 --action extract --extract-action text

  # 抓取页面链接
  python browser_api.py --tab 1 --action extract --extract-action links

  # 截图
  python browser_api.py --tab 1 --action screenshot --screenshot-action viewport --out shot.png

  # 标注截图
  python browser_api.py --tab 1 --action screenshot --screenshot-action annotate --out shot.png
        """
    )

    parser.add_argument("--tab", required=True, help="标签页 ID")
    parser.add_argument("--action", required=True, choices=["extract", "screenshot"], help="操作类型")

    # 抓取参数
    extract_group = parser.add_argument_group('抓取参数')
    extract_group.add_argument("--extract-action", choices=EXTRACT_ACTIONS, default="text", help="抓取模式")
    extract_group.add_argument("--selector", default=None, help="CSS 选择器或 XPath")
    extract_group.add_argument("--xpath", action="store_true", help="使用 XPath")
    extract_group.add_argument("--max-chars", type=int, default=20000, help="最大输出长度")
    extract_group.add_argument("--save", default=None, help="保存路径")

    # 截图参数
    screenshot_group = parser.add_argument_group('截图参数')
    screenshot_group.add_argument("--screenshot-action", choices=SCREENSHOT_ACTIONS, default="viewport", help="截图模式")
    screenshot_group.add_argument("--out", default=None, help="输出路径")
    screenshot_group.add_argument("--element-index", type=int, default=None, help="元素编号")
    screenshot_group.add_argument("--region", choices=["nav", "main", "sidebar", "content", "footer"], default=None, help="区域")
    screenshot_group.add_argument("--compare-path", default=None, help="对比截图路径")
    screenshot_group.add_argument("--zoom-factor", type=float, default=1.0, help="缩放倍数")
    screenshot_group.add_argument("--detail", action="store_true", help="详细标注")
    screenshot_group.add_argument("--no-scroll", action="store_true", help="不标注滚动外元素")
    screenshot_group.add_argument("--fmt", choices=["png", "jpeg"], default="png", help="输出格式")
    screenshot_group.add_argument("--quality", type=int, default=95, help="JPEG 质量")

    args = parser.parse_args()

    params = {}
    if args.action == "extract":
        params = {
            "action": args.extract_action,
            "selector": args.selector,
            "xpath": args.xpath,
            "max_chars": args.max_chars,
            "save": args.save,
        }
    elif args.action == "screenshot":
        params = {
            "action": args.screenshot_action,
            "out": args.out,
            "element_index": args.element_index,
            "region": args.region,
            "compare_path": args.compare_path,
            "zoom_factor": args.zoom_factor,
            "detail": args.detail,
            "no_scroll": args.no_scroll,
            "fmt": args.fmt,
            "quality": args.quality,
        }

    result = browser_api(args.tab, args.action, params)
    print(result.to_json())


if __name__ == "__main__":
    main()