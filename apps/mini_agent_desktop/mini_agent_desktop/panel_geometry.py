"""快速面板的窗口几何计算（纯逻辑，不依赖 Qt，便于单测）。

问题背景：面板原先只按鼠标位置平移、不设置尺寸，也不检查屏幕边界。
靠近屏幕底部/右侧时窗口会伸出屏幕外，只剩一小块可见。

这里统一做两件事：
  1. 尺寸：不超过目标尺寸，也不超过可用工作区（扣掉任务栏）；
  2. 位置：以鼠标为锚点，但整窗必须落在所在屏幕的可用区域内。
"""

from __future__ import annotations

from dataclasses import dataclass

PANEL_WIDTH = 560          # 目标宽度（逻辑像素）
PANEL_HEIGHT = 640         # 目标高度
MARGIN = 8                 # 距屏幕可用区域边缘的留白
CURSOR_OFFSET_Y = 60       # 面板顶部相对鼠标的上移量，让输入框落在鼠标附近


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    w: int
    h: int


def _clamp(value: int, lo: int, hi: int) -> int:
    return max(lo, min(value, hi))


def fit_panel(cursor: tuple[int, int], avail: Rect,
              want_w: int = PANEL_WIDTH, want_h: int = PANEL_HEIGHT) -> Rect:
    """返回面板应放置的矩形，保证整窗在 avail 之内（留 MARGIN）。

    avail 很小（比目标尺寸还小）时，尺寸缩到可用区域以内，而不是超出屏幕。
    """
    usable_w = max(avail.w - 2 * MARGIN, 1)
    usable_h = max(avail.h - 2 * MARGIN, 1)
    w = min(want_w, usable_w)
    h = min(want_h, usable_h)

    cx, cy = cursor
    x = _clamp(cx - w // 2, avail.x + MARGIN, avail.x + avail.w - w - MARGIN)
    y = _clamp(cy - CURSOR_OFFSET_Y, avail.y + MARGIN, avail.y + avail.h - h - MARGIN)
    return Rect(x, y, w, h)
