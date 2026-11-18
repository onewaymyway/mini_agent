"""compose/branding.py — 署名与水印（P6）：片头/片尾卡、下三分之一条、角标、水印图层与位置，BGM 设置。

所有图层都是程序绘制的整幅 RGBA PNG（或按需取用项目内的水印图片），不依赖模型，可随时重绘。
时间常量（秒）集中在此，供导出与 V6 校验共用。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..project_store import normalize_branding
from ..render.fonts import load_font
from ..render.text import wrap_text
from ..render.themes import get_theme, unit_for

TITLE_SEC = 4.0          # 片头标题卡时长
END_SEC = 5.0           # 片尾卡时长
LOWER_AFTER = 5.0       # 下三分之一条：片头结束后多少秒出现（设计 9.1：5～8 秒）
LOWER_SEC = 4.0         # 下三分之一条停留时长
DRIFT_PERIOD = (40.0, 53.0)  # 漂移水印横向/纵向周期（秒）
BGM_EXTS = (".mp3", ".wav", ".m4a", ".aac", ".ogg", ".flac")


def resolve_branding(project: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    """以项目 branding 为准，缺省时按归一化规则补齐。"""
    return normalize_branding(project.get("branding"))


def resolve_bgm(project_dir: Path, project: Dict[str, Any]) -> Tuple[Dict[str, Any], List[str]]:
    raw = project.get("bgm") if isinstance(project.get("bgm"), dict) else {}
    warnings: List[str] = []
    try:
        vol = min(1.0, max(0.0, float(raw.get("volume", 0.25))))
    except (TypeError, ValueError):
        vol = 0.25
        warnings.append("BGM 音量不是数字，已改用 0.25")
    rel = str(raw.get("path") or "").strip()
    path = Path(project_dir) / rel if rel else None
    enabled = bool(raw.get("enabled", False))
    if enabled and (path is None or not path.is_file() or path.suffix.lower() not in BGM_EXTS):
        warnings.append("BGM 未启用：未找到可用的本地音频文件（需要用户提供，不会自动下载或生成）")
        enabled = False
    return {"enabled": enabled, "path": str(path) if path and enabled else None, "volume": round(vol, 2)}, warnings


def _unit(canvas, orientation):
    return unit_for(canvas, orientation)


def render_credit_card(canvas, orientation: str, theme_name: str, lines: List[Tuple[str, str]]):
    """居中堆叠的卡片文字。role: title / sub / small / muted。整幅 RGBA（透明底）。"""
    from PIL import Image, ImageDraw
    u = _unit(canvas, orientation)
    W, H = canvas
    theme = get_theme(theme_name)
    img = Image.new("RGBA", canvas, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img, "RGBA")
    sizes = {"title": (96 if orientation != "portrait" else 78, theme["text"], 20),
             "sub": (46 if orientation != "portrait" else 40, theme["text"], 16),
             "small": (34 if orientation != "portrait" else 30, theme["muted"], 12),
             "muted": (34 if orientation != "portrait" else 30, theme["muted"], 12)}
    maxw = W * (0.8 if orientation != "portrait" else 0.9)
    blocks = []
    for text, role in lines:
        if not text.strip():
            continue
        size, color, gap = sizes.get(role, sizes["sub"])
        font = load_font(round(size * u))
        wrapped = wrap_text(draw, text, font, int(maxw), max_lines=3 if role == "title" else 2)
        blocks.append((wrapped, font, color, round(gap * u)))
    total = sum(len(w) * f.size + (len(w) - 1) * 6 * u + g for w, f, _c, g in blocks)
    y = (H - total) / 2
    for wrapped, font, color, gap in blocks:
        lh = font.size + round(6 * u)
        for line in wrapped:
            lw = draw.textlength(line, font=font)
            draw.text(((W - lw) / 2, y), line, font=font, fill=color)
            y += lh
        y += gap
    return img


def render_lower_third(canvas, orientation: str, theme_name: str, text: str):
    """下三分之一条：左下方半透明条带 + 强调色竖线。"""
    from PIL import Image, ImageDraw
    u = _unit(canvas, orientation)
    W, H = canvas
    theme = get_theme(theme_name)
    img = Image.new("RGBA", canvas, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img, "RGBA")
    font = load_font(round((40 if orientation != "portrait" else 34) * u))
    maxw = W * (0.8 if orientation != "portrait" else 0.86)
    lines = wrap_text(draw, text, font, int(maxw - 60 * u), max_lines=1)
    if not lines:
        return img
    bw = draw.textlength(lines[0], font=font) + 60 * u
    bh = font.size + 36 * u
    x0, y0 = W * 0.05, H * 0.78
    draw.rounded_rectangle([x0, y0, x0 + bw, y0 + bh], radius=10 * u, fill=(0, 0, 0, 160))
    draw.rectangle([x0, y0, x0 + 8 * u, y0 + bh], fill=theme["accent"])
    draw.text((x0 + 30 * u, y0 + 16 * u), lines[0], font=font, fill=theme["text"])
    return img


def render_corner_tag(canvas, orientation: str, theme_name: str, text: str):
    from PIL import Image, ImageDraw
    u = _unit(canvas, orientation)
    W, H = canvas
    theme = get_theme(theme_name)
    img = Image.new("RGBA", canvas, (0, 0, 0, 0))
    draw = ImageDraw.Draw(img, "RGBA")
    font = load_font(round(30 * u))
    tw = draw.textlength(text, font=font)
    x0, y0 = W * 0.03, H * 0.03
    draw.rounded_rectangle([x0, y0, x0 + tw + 28 * u, y0 + font.size + 20 * u], radius=8 * u,
                           fill=(0, 0, 0, 110))
    draw.text((x0 + 14 * u, y0 + 9 * u), text, font=font, fill=theme["muted"])
    return img


def render_watermark(canvas, orientation: str, theme_name: str, wm: Dict[str, Any], project_dir: Path,
                     *, tile: bool = False):
    """水印图层。返回 (RGBA 图, 单个水印的宽高)。tile=True 时返回整幅斜向平铺图。"""
    from PIL import Image, ImageDraw, ImageEnhance
    W, H = canvas
    theme = get_theme(theme_name)
    opacity = float(wm.get("opacity", 0.35))
    scale = float(wm.get("scale", 0.06))
    if wm.get("type") == "image" and wm.get("image"):
        src = Image.open(Path(project_dir) / wm["image"]).convert("RGBA")
        target_w = max(16, round(W * scale * 3))
        mark = src.resize((target_w, round(src.height * target_w / src.width)), Image.LANCZOS)
    else:
        font = load_font(max(12, round(W * scale * 0.5)))
        tmp = Image.new("RGBA", (10, 10))
        tw = int(ImageDraw.Draw(tmp).textlength(str(wm.get("text", "")), font=font)) + 8
        mark = Image.new("RGBA", (tw, font.size + 12), (0, 0, 0, 0))
        ImageDraw.Draw(mark).text((4, 4), str(wm.get("text", "")), font=font, fill=theme["text"])
    alpha = mark.getchannel("A")
    mark.putalpha(ImageEnhance.Brightness(alpha).enhance(opacity))
    if not tile:
        return mark, mark.size
    layer = Image.new("RGBA", canvas, (0, 0, 0, 0))
    rot = mark.rotate(30, expand=True, resample=Image.BICUBIC)
    step_x, step_y = rot.width + round(W * 0.12), rot.height + round(H * 0.10)
    for row, y in enumerate(range(-rot.height, H + step_y, step_y)):
        offset = (step_x // 2) if row % 2 else 0
        for x in range(-rot.width + offset, W + step_x, step_x):
            _paste(layer, rot, int(x), int(y))
    return layer, (W, H)


def _paste(dst, src, x, y):
    # 允许越界裁剪的粘贴
    from PIL import Image
    tmp = Image.new("RGBA", dst.size, (0, 0, 0, 0))
    tmp.paste(src, (x, y))
    dst.alpha_composite(tmp)


def watermark_xy(canvas, size: Tuple[int, int], position: str, margin_frac: float) -> Tuple[int, int]:
    W, H = canvas
    w, h = size
    m = round(W * margin_frac)
    pos = {
        "top_left": (m, m), "top_right": (W - w - m, m),
        "bottom_left": (m, H - h - m), "bottom_right": (W - w - m, H - h - m),
        "center": ((W - w) // 2, (H - h) // 2),
    }
    return pos.get(position, pos["top_right"])


def drift_expressions(canvas, size: Tuple[int, int], margin_frac: float) -> Tuple[str, str]:
    """漂移模式：水印在安全区内缓慢往复移动（正弦），位于所有动效之上，不随镜头晃动。"""
    W, H = canvas
    m = round(W * margin_frac)
    px = f"{m}+(main_w-overlay_w-2*{m})*(0.5+0.5*sin(2*PI*t/{DRIFT_PERIOD[0]:.1f}))"
    py = f"{m}+(main_h-overlay_h-2*{m})*(0.5+0.5*cos(2*PI*t/{DRIFT_PERIOD[1]:.1f}))"
    return px, py


def inside_canvas(canvas, box: Tuple[int, int, int, int]) -> bool:
    """水印框是否完整位于画面内（V6 安全区检查的基础）。"""
    W, H = canvas
    x, y, w, h = box
    return x >= 0 and y >= 0 and x + w <= W and y + h <= H


def save_bgm(project_dir: Path, *, enabled: bool, rel_path: str, volume: float) -> Dict[str, Any]:
    """保存 BGM 设置到 project.json（只接受项目内的相对路径；音频须由用户提供）。"""
    from ..project_store import load_project, write_json_atomic
    project_dir = Path(project_dir)
    project = load_project(project_dir)
    rel = str(rel_path or "").strip().replace("\\", "/")
    if rel.startswith("/") or ".." in Path(rel).parts:
        rel = ""
    project["bgm"] = {"enabled": bool(enabled), "path": rel, "volume": round(min(1.0, max(0.0, float(volume))), 2)}
    write_json_atomic(project_dir / "project.json", project)
    cfg, warn = resolve_bgm(project_dir, project)
    return {"config": cfg, "warnings": warn}
