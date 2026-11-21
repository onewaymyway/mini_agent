"""新方案的页面排版（R30）：一页多个元素时，尽量不靠“整体缩小位图”来塞进页面。

旧做法：每个元素都按“独占整页”的大字号各自渲染，裁掉留白后纵向堆叠；放不下就把所有位图整体等比缩小，
于是内容多的页文字被缩得很小，而且从来不分列。

现做法（单栏优先，不够再分栏）：
  1. 单栏：按各元素自然高度的比例分配高度预算，让渲染器在预算内自己缩字号、换行、分栏（binary search 找到最大可行预算）；
     固定字号的元素（表格、金句、公式）无法在预算内缩小时，才对整体做最后的等比缩小。
  2. 实际字号 = 渲染字号 × 缩放比例。低于下限（FLOOR_BASE × 单位）且允许分栏时，把元素按顺序分成左右两栏
     （枚举分割点，取实际字号最大者），两栏各自按同样方法排版；只有分栏明显更好时才采用。
  3. 仍低于下限：不再硬缩，保留最佳结果并标记 small=True，由调用方提示（规划阶段应拆页）。
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple

from PIL import Image

from ..render.renderers import render_scene_final
from ..render.themes import unit_for

FLOOR_BASE = {"landscape": 28.0, "portrait": 24.0}   # 正文最小字号（按 1920/1080 宽为 1.0 的单位）
COLUMN_GAIN = 1.08                                   # 分栏的实际字号至少比单栏大 8% 才采用
SEARCH_STEPS = 7


def _crop(img) -> Optional[Any]:
    box = img.getchannel("A").getbbox()
    return img.crop(box) if box else None


ADAPTIVE_TYPES = ("keypoint", "list", "diagram")   # 渲染器会按画布高度缩字号/分栏的类型；其余类型字号固定


def _solve_column(elements: List[Dict[str, Any]], width: int, body_h: int, gap: int, orientation: str,
                  theme: str, unit: float, scene_for: Callable) -> Dict[str, Any]:
    """把若干元素排成一栏。返回 {pieces:[(img, y)], height, effective_px}。
    渲染结果贴着画布上/下边缘说明被截断，该预算视为不可行（绝不使用被截断的画面）。"""
    scenes = [scene_for(el) for el in elements]

    def render_one(sc, h, un=unit):
        img, px = render_scene_final(sc, (width, h), orientation, theme, unit=un)
        box = img.getchannel("A").getbbox()
        if box is None:
            return None
        clipped = box[1] <= 0 or box[3] >= h
        return img.crop(box), px, clipped

    natural = []
    for sc in scenes:
        r = render_one(sc, body_h)
        if r is not None:
            natural.append(r)
    if not natural:
        return {"pieces": [], "height": 0, "effective_px": None, "scale": 1.0}

    def total_of(pieces):
        return sum(p.size[1] for p, _ in pieces) + gap * max(0, len(pieces) - 1)

    pieces = [(p, px) for p, px, _ in natural]
    if total_of(pieces) > body_h:
        # 预算：renderer 可用高度约为画布高度的 80%，所以画布高 = 目标内容高 / 0.8；固定字号的类型按同一比例缩小字号基准
        nat_h = [p.size[1] for p, _ in pieces]
        idx = [i for i, sc in enumerate(scenes) if i < len(pieces)]
        lo, hi, best = 0.15, 1.0, None
        for _ in range(SEARCH_STEPS):
            k = (lo + hi) / 2
            trial, ok = [], True
            for i in idx:
                if scenes[i].get("type", "keypoint") not in ADAPTIVE_TYPES:
                    # 固定字号的类型（表格、金句、公式）：用同一比例缩小字号基准重新渲染（保持清晰，不缩放位图）
                    r = render_one(scenes[i], body_h, unit * k)
                    if r is None or r[2]:
                        ok = False
                        break
                    trial.append((r[0], r[1]))
                    continue
                r = render_one(scenes[i], max(60, int(nat_h[i] * k / 0.8)))
                if r is None or r[2]:
                    ok = False
                    break
                trial.append((r[0], r[1]))
            if ok and total_of(trial) <= body_h:
                best, lo = trial, k
            else:
                hi = k
        if best is not None:
            pieces = best
    f = 1.0
    t = total_of(pieces)
    if t > body_h:                                  # 仍放不下（固定字号元素太多，或没有可行预算）：最后才整体缩小
        f = (body_h - gap * max(0, len(pieces) - 1)) / max(1, sum(p.size[1] for p, _ in pieces))
        pieces = [(p.resize((max(1, int(p.size[0] * f)), max(1, int(p.size[1] * f))), Image.LANCZOS), px)
                  for p, px in pieces]
        t = total_of(pieces)
    fonts = [px * f for _, px in pieces if px]
    placed, y = [], max(0, (body_h - t) // 2)
    for p, _ in pieces:
        placed.append((p, y))
        y += p.size[1] + gap
    return {"pieces": placed, "height": t, "effective_px": min(fonts) if fonts else None, "scale": f}


def layout_page(elements: List[Dict[str, Any]], canvas: Tuple[int, int], body_h: int, gap: int, orientation: str,
                theme: str, scene_for: Callable, *, columns: bool = True) -> Dict[str, Any]:
    """返回 {"pieces": [(img, x, y)]（与元素同序）, "mode": "single"|"columns", "split": k, "effective_px",
    "floor_px", "small": bool}。坐标相对正文区左上角。"""
    W, _H = canvas
    u = unit_for(canvas, orientation)
    floor_px = FLOOR_BASE["portrait" if orientation == "portrait" else "landscape"] * u
    one = _solve_column(elements, W, body_h, gap, orientation, theme, u, scene_for)
    best = {"mode": "single", "split": 0, "effective_px": one["effective_px"],
            "pieces": [(p, int((W - p.size[0]) / 2), y) for p, y in one["pieces"]]}
    eff1 = one["effective_px"]
    if columns and len(elements) >= 2 and eff1 is not None and eff1 < floor_px:
        colw = int(W * 0.47)
        gap_x = W - 2 * colw
        trial_best = None
        for k in range(1, len(elements)):
            left = _solve_column(elements[:k], colw, body_h, gap, orientation, theme, u, scene_for)
            right = _solve_column(elements[k:], colw, body_h, gap, orientation, theme, u, scene_for)
            effs = [e for e in (left["effective_px"], right["effective_px"]) if e is not None]
            if not effs or len(left["pieces"]) + len(right["pieces"]) != len(one["pieces"]):
                continue
            eff = min(effs)
            if trial_best is None or eff > trial_best[0]:
                trial_best = (eff, k, left, right)
        if trial_best and trial_best[0] > eff1 * COLUMN_GAIN:
            eff, k, left, right = trial_best
            x_l, x_r = gap_x // 2, gap_x // 2 + colw
            pcs = [(p, x_l + int((colw - p.size[0]) / 2), y) for p, y in left["pieces"]]
            pcs += [(p, x_r + int((colw - p.size[0]) / 2), y) for p, y in right["pieces"]]
            best = {"mode": "columns", "split": k, "effective_px": eff, "pieces": pcs}
    eff = best["effective_px"]
    best.update(floor_px=round(floor_px, 1), small=bool(eff is not None and eff < floor_px))
    return best
