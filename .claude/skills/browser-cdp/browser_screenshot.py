"""
browser_screenshot.py - 截图

用法：
  python browser_screenshot.py --tab <id> --out shot.png                 # 可视区域截图
  python browser_screenshot.py --tab <id> --out shot.png --full-page      # 整页截图
  python browser_screenshot.py --tab <id> --out shot.png --annotate       # 编号标注可交互元素（computer-use 风格）
  python browser_screenshot.py --tab <id> --out shot.png --element-index 3  # 只截某个元素

标注模式下会同时输出一份 `<out>.elements.json`，记录每个编号对应的元素信息，
配合 browser_input.py --click-index / --type-index 使用，形成"看图 -> 定位 -> 操作"的闭环。
"""
from __future__ import annotations

import argparse
import base64
import json
import os

from utils import add_connection_args, get_session, scan_interactive_elements


def capture(session, full_page: bool, clip: dict | None = None) -> bytes:
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
    result = session.send("Page.captureScreenshot", params)
    return base64.b64decode(result["data"])


def annotate_png(png_bytes: bytes, elements: list[dict]) -> bytes:
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

    colors = ["#FF3B30", "#007AFF", "#34C759", "#FF9500", "#AF52DE"]
    for el in elements:
        if not el.get("inViewport", True):
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

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return buf.getvalue()


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    add_connection_args(parser)
    parser.add_argument("--out", required=True, help="输出 png 路径")
    parser.add_argument("--full-page", action="store_true")
    parser.add_argument("--annotate", action="store_true", help="标注可交互元素编号")
    parser.add_argument("--element-index", type=int, default=None, help="只截取指定编号元素的区域（需先用 --mode elements 或本参数触发扫描）")

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

        png_bytes = capture(session, full_page=args.full_page, clip=clip)

        if args.annotate and clip is None:
            png_bytes = annotate_png(png_bytes, elements)

        with open(args.out, "wb") as f:
            f.write(png_bytes)

        if args.annotate:
            side_path = os.path.splitext(args.out)[0] + ".elements.json"
            with open(side_path, "w", encoding="utf-8") as f:
                json.dump(elements, f, ensure_ascii=False, indent=2)
            print(f"[ok] 截图已保存: {args.out}\n[ok] 元素编号表: {side_path}")
        else:
            print(f"[ok] 截图已保存: {args.out}")
    finally:
        session.close()


if __name__ == "__main__":
    main()
