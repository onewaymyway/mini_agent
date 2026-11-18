"""render/fonts.py — 中文字体查找。未找到时明确报错，绝不画成方框。

优先级：环境变量 ARTICLE_TO_VIDEO_FONT（.ttf/.ttc/.otf 路径）→ 常见系统字体候选。
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

FONT_ENV = "ARTICLE_TO_VIDEO_FONT"

CANDIDATES: List[str] = [
    # Windows
    r"C:\Windows\Fonts\msyh.ttc", r"C:\Windows\Fonts\msyhbd.ttc", r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\simsun.ttc",
    # macOS
    "/System/Library/Fonts/PingFang.ttc", "/System/Library/Fonts/STHeiti Medium.ttc",
    "/System/Library/Fonts/Supplemental/Songti.ttc",
    # Linux（Noto / 文泉驿）
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Medium.ttc",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
]


class FontError(RuntimeError):
    pass


def find_cjk_font(env: Optional[dict] = None) -> str:
    env = os.environ if env is None else env
    explicit = env.get(FONT_ENV, "").strip()
    if explicit:
        if Path(explicit).is_file():
            return explicit
        raise FontError(f"{FONT_ENV} 指向的字体不存在：{explicit}")
    for cand in CANDIDATES:
        if Path(cand).is_file():
            return cand
    raise FontError(f"找不到中文字体。请设置环境变量 {FONT_ENV} 指向一个 .ttf/.ttc/.otf 文件。")


@lru_cache(maxsize=64)
def load_font(size_px: int):
    from PIL import ImageFont

    return ImageFont.truetype(find_cjk_font(), max(8, int(size_px)))
