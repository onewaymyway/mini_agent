#!/usr/bin/env python3
"""render_cover_title.py — novel-video-studio 阶段3 Step B（可选，仅
`novel_project.json.cover.enabled == true` 时使用）：给阶段3 Step A
生成的、不含文字的剧情向背景图 `cover_bg.png` 叠加小说标题文字，产出
最终封面 `cover.png`。

设计动机（见 `next_doc/novel_video_studio_fix_plan_v7.md`）：
  - 文生图模型画中文字几乎必错，所以背景图 `cover_bg.png` 生成时明确
    要求"no text"，标题完全交给本脚本用本地 PIL 后期烧上去；
  - 本脚本只做本地图像处理，不调用任何外部 API，免费、秒级完成——
    这样用户只想换标题文字/布局时，不需要重新生成背景图，直接重跑
    本脚本即可（见 `references/revision_and_rollback.md` §事后补建封面）；
  - 字体探测复用 `common.py` 的 `resolve_font_path()`，和
    `compose_macro_scene.py` 里字幕烧字用的是同一套优先级
    （--font-path > NOVEL_FONT_PATH 环境变量 > 跨平台常见字体候选路径）。

用法：
    python render_cover_title.py <bg_image> <out_image> \
        --title "小说标题" [--layout center|bottom_bar|top_classic] \
        [--font-path <字体路径>] [--font-size <像素，不传按图片尺寸自适应>]

产物：<out_image>（默认与 <bg_image> 分辨率一致的 PNG/JPEG，取决于
<out_image> 后缀）。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from common import resolve_font_path

LAYOUTS = ("center", "bottom_bar", "top_classic")


def _wrap_lines(draw, text: str, font, max_w: int) -> list[str]:
    """按最大像素宽度给标题文字换行。text 里的显式 \\n 优先保留为
    分段边界，每段再按像素宽度贪心换行（与 compose_macro_scene.py 里
    字幕换行算法一致的思路，独立实现避免脚本间互相 import）。"""
    lines: list[str] = []
    for raw_line in text.split("\n"):
        raw_line = raw_line.strip()
        if not raw_line:
            continue
        cur = ""
        for ch in raw_line:
            trial = cur + ch
            tw = draw.textlength(trial, font=font) if hasattr(draw, "textlength") \
                else sum(font.getlength(c) for c in trial)
            if tw > max_w and cur:
                lines.append(cur)
                cur = ch
            else:
                cur = trial
        if cur:
            lines.append(cur)
    return lines or [text.strip()]


def _draw_centered_line(draw, line: str, font, cy: int, W: int,
                         fill=(255, 255, 255, 255), stroke_fill=None, stroke_width=0):
    tw = draw.textlength(line, font=font) if hasattr(draw, "textlength") \
        else sum(font.getlength(c) for c in line)
    x = (W - tw) / 2
    kwargs = {}
    if stroke_width:
        kwargs["stroke_width"] = stroke_width
        kwargs["stroke_fill"] = stroke_fill
    draw.text((x, cy), line, font=font, fill=fill, **kwargs)


def render(bg_path: Path, out_path: Path, title: str, layout: str,
           font_path: str, font_size: int | None) -> None:
    from PIL import Image, ImageDraw, ImageFont

    if layout not in LAYOUTS:
        raise ValueError(f"未知 layout: {layout!r}，可选值：{LAYOUTS}")

    base = Image.open(bg_path).convert("RGBA")
    W, H = base.size
    overlay = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)

    # 字号未显式指定时按图片短边自适应，保证不同分辨率下视觉比例一致。
    fs = font_size or max(28, int(min(W, H) * 0.075))
    font = ImageFont.truetype(font_path, fs)

    max_w = int(W * 0.82)
    lines = _wrap_lines(draw, title, font, max_w)
    line_h = int(fs * 1.35)
    block_h = line_h * len(lines)

    if layout == "center":
        # 居中偏上1/3处，半透明深色蒙层衬底，通用海报风格默认布局。
        band_pad = int(fs * 0.6)
        band_y0 = int(H * 0.30) - band_pad
        band_y1 = band_y0 + block_h + band_pad * 2
        draw.rectangle([0, max(0, band_y0), W, min(H, band_y1)], fill=(0, 0, 0, 120))
        y = band_y0 + band_pad
        for line in lines:
            _draw_centered_line(draw, line, font, y, W,
                                 fill=(255, 255, 255, 255),
                                 stroke_fill=(0, 0, 0, 200), stroke_width=max(1, fs // 18))
            y += line_h

    elif layout == "bottom_bar":
        # 底部半透明色块条，标题放条内，不遮挡画面主体构图。
        bar_pad = int(fs * 0.5)
        bar_h = block_h + bar_pad * 2
        bar_y0 = H - bar_h
        draw.rectangle([0, bar_y0, W, H], fill=(0, 0, 0, 150))
        y = bar_y0 + bar_pad
        for line in lines:
            _draw_centered_line(draw, line, font, y, W,
                                 fill=(255, 255, 255, 255),
                                 stroke_fill=(0, 0, 0, 180), stroke_width=max(1, fs // 20))
            y += line_h

    else:  # top_classic
        # 画面上方留白处放标题，无蒙层，用描边保证在浅色背景上也可读。
        y = int(H * 0.08)
        for line in lines:
            _draw_centered_line(draw, line, font, y, W,
                                 fill=(255, 255, 255, 255),
                                 stroke_fill=(0, 0, 0, 220), stroke_width=max(2, fs // 14))
            y += line_h

    composed = Image.alpha_composite(base, overlay)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if out_path.suffix.lower() in (".jpg", ".jpeg"):
        composed.convert("RGB").save(out_path, "JPEG", quality=95)
    else:
        composed.save(out_path, "PNG")


def main():
    parser = argparse.ArgumentParser(description="给封面背景图叠加小说标题文字")
    parser.add_argument("bg_image", type=Path, help="阶段3 Step A 生成的 cover_bg.png")
    parser.add_argument("out_image", type=Path, help="输出路径，默认写 cover.png")
    parser.add_argument("--title", required=True, help="标题文字（novel_project.json.cover.title_text 或 source_title）")
    parser.add_argument("--layout", default="center", choices=LAYOUTS,
                         help="标题排布方式，默认 center（居中+蒙层）")
    parser.add_argument("--font-path", default=None,
                         help="标题字体文件路径，不传则依次尝试 NOVEL_FONT_PATH "
                              "环境变量、跨平台常见字体候选路径，都找不到时报错")
    parser.add_argument("--font-size", type=int, default=None,
                         help="字号（像素），不传则按图片短边自适应")
    args = parser.parse_args()

    if not args.bg_image.exists():
        print(f"❌ 找不到背景图: {args.bg_image}", file=sys.stderr)
        sys.exit(2)

    try:
        font_path = resolve_font_path(args.font_path)
    except RuntimeError as e:
        print(f"❌ {e}", file=sys.stderr)
        sys.exit(2)

    title = args.title.strip()
    if not title:
        print("❌ --title 不能为空字符串", file=sys.stderr)
        sys.exit(2)

    print(f"[env] font={font_path} layout={args.layout}")
    render(args.bg_image, args.out_image, title, args.layout, font_path, args.font_size)
    print(f"Cover with title rendered: {args.out_image}")


if __name__ == "__main__":
    main()
