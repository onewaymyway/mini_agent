"""
browser_screenshot.py - 截图（增强版）

用法：
  python browser_screenshot.py --tab <id> --out shot.png                 # 可视区域截图
  python browser_screenshot.py --tab <id> --out shot.png --full-page      # 整页截图
  python browser_screenshot.py --tab <id> --out shot.png --annotate       # 编号标注可交互元素（computer-use 风格）
  python browser_screenshot.py --tab <id> --out shot.png --element-index 3  # 只截某个元素
  python browser_screenshot.py --tab <id> --out shot.png --region main    # 智能区域截图（main/nav/sidebar）
  python browser_screenshot.py --tab <id> --out shot.png --annotate --detail  # 详细标注（含元素类型）
  python browser_screenshot.py --tab <id> --out shot.png --annotate --no-scroll  # 不标注滚动外元素
  python browser_screenshot.py --tab <id> --out shot.png --quality 80      # JPEG 质量（默认 PNG）
  python browser_screenshot.py --tab <id> --out shot.png --format jpeg     # 输出 JPEG 格式
  python browser_screenshot.py --tab <id> --out shot.png --zoom 1.5        # 缩放截图（高清）
  python browser_screenshot.py --tab <id> --out shot.png --compare shot2.png  # 对比两张截图差异
  python browser_screenshot.py --tab <id> --out shot.png --highlight-changed  # 高亮变化区域

标注模式下会同时输出一份 `<out>.elements.json`，记录每个编号对应的元素信息，
配合 browser_input.py --click-index / --type-index 使用，形成"看图 -> 定位 -> 操作"的闭环。
"""
from __future__ import annotations

import argparse
import base64
import json
import os

from src.core.utils import add_connection_args, get_session, scan_interactive_elements
from src.reliability.middleware import (
    get_middleware,
    OperationType,
    with_error_handling,
)
from src.reliability.error import (
    ElementNotFoundError,
    CDPConnectionLostError,
)


@with_error_handling("capture_screenshot", OperationType.SCREENSHOT, max_retries=2)
def capture(session, full_page: bool, clip: dict | None = None, timeout: float = 60.0) -> bytes:
    params = {"format": "png"}
    if clip:
        params["clip"] = clip
        params["captureBeyondViewport"] = True
    elif full_page:
        metrics = session.send("Page.getLayoutMetrics")
        content_size = metrics.get("cssContentSize") or metrics.get("contentSize")
        params["clip"] = {
            "x": 0,
            "y": 0,
            "width": content_size["width"],
            "height": content_size["height"],
            "scale": 1,
        }
        params["captureBeyondViewport"] = True
    result = session.send("Page.captureScreenshot", params, timeout=timeout)
    return base64.b64decode(result["data"])


def annotate_png(png_bytes: bytes, elements: list[dict], detail: bool = False, no_scroll: bool = False) -> bytes:
    """增强标注：支持详细标注、过滤滚动外元素"""
    try:
        from PIL import Image, ImageDraw, ImageFont
    except ImportError:
        raise SystemExit("需要 Pillow：pip install pillow")

    import io

    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    draw = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default()
    except Exception:
        font = None

    colors = ["#FF3B30", "#007AFF", "#34C759", "#FF9500", "#AF52DE", "#00C853", "#AA00FF", "#FFD600"]
    for el in elements:
        if not no_scroll and not el.get("inViewport", True):
            continue
        r = el["rect"]
        x0, y0 = r["x"], r["y"]
        x1, y1 = x0 + r["width"], y0 + r["height"]
        color = colors[el["index"] % len(colors)]
        draw.rectangle([x0, y0, x1, y1], outline=color, width=2)
        label = str(el["index"])
        # 标签背景
        text_w = 7 * len(label) + 6
        draw.rectangle([x0, max(0, y0 - 16), x0 + text_w, y0], fill=color)
        draw.text((x0 + 2, max(0, y0 - 15)), label, fill="white", font=font)
        # 详细标注：显示元素类型
        if detail:
            tag = el.get("tag", "unknown")
            tag_label = f"{label}:{tag}"
            tag_w = 8 * len(tag_label) + 6
            draw.rectangle([x0, max(0, y0 - 32), x0 + tag_w, y0 - 16], fill=color)
            draw.text((x0 + 2, max(0, y0 - 31)), tag_label, fill="white", font=font)

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def compare_screenshots(png1: bytes, png2: bytes, highlight: bool = False) -> bytes:
    """对比两张截图，高亮差异区域"""
    try:
        from PIL import Image, ImageChops
    except ImportError:
        raise SystemExit("需要 Pillow：pip install pillow")

    import io

    img1 = Image.open(io.BytesIO(png1)).convert("RGB")
    img2 = Image.open(io.BytesIO(png2)).convert("RGB")

    # 确保尺寸一致
    if img1.size != img2.size:
        img2 = img2.resize(img1.size)

    diff = ImageChops.difference(img1, img2)

    if highlight:
        # 高亮差异区域
        bbox = diff.getbbox()
        if bbox:
            result = img1.copy()
            draw = ImageDraw.Draw(result)
            draw.rectangle(bbox, outline="#FF0000", width=3)
            return result

    # 叠加差异图
    diff_img = Image.new("RGB", img1.size)
    diff_pixels = diff.load()
    img1_pixels = img1.load()
    img2_pixels = img2.load()
    for x in range(img1.width):
        for y in range(img1.height):
            p1 = img1_pixels[x, y]
            p2 = img2_pixels[x, y]
            if abs(p1[0] - p2[0]) > 30 or abs(p1[1] - p2[1]) > 30 or abs(p1[2] - p2[2]) > 30:
                diff_img.putpixel((x, y), (255, 0, 0))
            else:
                diff_img.putpixel((x, y), p1)

    return diff_img


def smart_region_crop(png_bytes: bytes, region: str) -> bytes:
    """智能裁剪指定区域：main/nav/sidebar/content"""
    try:
        from PIL import Image
    except ImportError:
        raise SystemExit("需要 Pillow：pip install pillow")

    import io

    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    w, h = img.size

    regions = {
        "nav": (0, 0, w, int(h * 0.15)),
        "main": (0, int(h * 0.15), w, int(h * 0.7)),
        "sidebar": (int(w * 0.7), int(h * 0.15), w, int(h * 0.7)),
        "content": (0, int(h * 0.15), int(w * 0.7), int(h * 0.85)),
        "footer": (0, int(h * 0.85), w, h),
    }

    if region not in regions:
        raise SystemExit(f"未知区域: {region}，可选: {list(regions.keys())}")

    return img.crop(regions[region])


def zoom_screenshot(png_bytes: bytes, zoom: float = 1.5) -> bytes:
    """缩放截图（高清模式）"""
    try:
        from PIL import Image
    except ImportError:
        raise SystemExit("需要 Pillow：pip install pillow")

    import io

    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    new_size = (int(img.width * zoom), int(img.height * zoom))
    return img.resize(new_size, Image.LANCZOS)


def save_screenshot(png_bytes: bytes, path: str, fmt: str = "png", quality: int = 95) -> None:
    """保存截图，支持 PNG/JPEG"""
    try:
        from PIL import Image
    except ImportError:
        raise SystemExit("需要 Pillow：pip install pillow")

    import io

    img = Image.open(io.BytesIO(png_bytes)).convert("RGB")
    if fmt == "jpeg":
        img.save(path, format="JPEG", quality=quality)
    else:
        img.save(path, format="PNG")


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_connection_args(parser)
    parser.add_argument("--out", required=True, help="输出路径")
    parser.add_argument("--full-page", action="store_true")
    parser.add_argument("--annotate", action="store_true", help="标注可交互元素编号")
    parser.add_argument("--element-index", type=int, default=None, help="只截取指定编号元素的区域")
    parser.add_argument("--timeout", type=float, default=60.0, help="截图超时时间（秒）")
    parser.add_argument("--region", choices=["nav", "main", "sidebar", "content", "footer"], default=None, help="智能区域截图")
    parser.add_argument("--detail", action="store_true", help="详细标注（含元素类型）")
    parser.add_argument("--no-scroll", action="store_true", help="不标注滚动外元素")
    parser.add_argument("--format", choices=["png", "jpeg"], default="png", help="输出格式")
    parser.add_argument("--quality", type=int, default=95, help="JPEG 质量（1-100）")
    parser.add_argument("--zoom", type=float, default=1.0, help="缩放倍数（高清模式）")
    parser.add_argument("--compare", default=None, help="对比另一张截图")
    parser.add_argument("--highlight-changed", action="store_true", help="高亮差异区域")

    args = parser.parse_args()
    session = get_session(args)
    try:
        elements = []
        if args.annotate or args.element_index is not None:
            elements = scan_interactive_elements(session)

        clip = None
        if args.element_index is not None:
            target = next((e for e in elements if e["index"] == args.element_index), None)
            if not target:
                raise SystemExit(f"未找到编号为 {args.element_index} 的元素")
            r = target["rect"]
            clip = {"x": r["x"], "y": r["y"], "width": r["width"], "height": r["height"], "scale": 1}

        png_bytes = capture(session, full_page=args.full_page, clip=clip, timeout=args.timeout)

        # 智能区域裁剪
        if args.region:
            png_bytes = smart_region_crop(png_bytes, args.region)

        # 缩放
        if args.zoom > 1.0:
            png_bytes = zoom_screenshot(png_bytes, args.zoom)

        # 标注
        if args.annotate and clip is None:
            png_bytes = annotate_png(png_bytes, elements, detail=args.detail, no_scroll=args.no_scroll)

        # 对比
        if args.compare:
            with open(args.compare, "rb") as f:
                other_bytes = f.read()
            png_bytes = compare_screenshots(png_bytes, other_bytes, highlight=args.highlight_changed)

        # 保存
        save_screenshot(png_bytes, args.out, fmt=args.format, quality=args.quality)
        print(f"[ok] 截图已保存: {args.out}")

        if args.annotate:
            side_path = os.path.splitext(args.out)[0] + ".elements.json"
            with open(side_path, "w", encoding="utf-8") as f:
                json.dump(elements, f, ensure_ascii=False, indent=2)
            print(f"[ok] 元素编号表: {side_path}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
