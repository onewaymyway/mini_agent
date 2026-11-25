"""world_forge/comic_layout.py — 漫画版式与排字规划（纯逻辑，不依赖图像库）。

同一份规划结果同时供两处使用：
  * comic_render.py：服务端排字，输出 PNG 页（权威成品）；
  * engine.render_comic_html：HTML 预览（不含尾巴，仅作快速查看）。

版式：每页的格子先按 span 分行（整行格独占一行，连续的半行格两两配对），
行高按行类型加权（整行格更高，突出高潮），再按阅读方向决定同一行内的左右顺序。

排字：对白按 position 落入 6 个区域之一；近景/特写的中间区域会让开人脸，
改为靠左或靠右；尾巴指向说话人在画面中的大致位置（speaker_side）。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

PAGE_W = 1200          # 页面像素宽度（PNG）
PAGE_H = 1700          # 页面像素高度（约 1:1.414，接近 A 系列纸）
PAGE_MARGIN = 0.02     # 页边距（占页面比例）
GUTTER_PX = 16         # 格间距（像素）
DEFAULT_PAGES = "auto"   # 默认按故事长度（情节数）决定页数；固定页数只用于明确指定时
MAX_PAGES = 20         # 显式指定页数的上限；auto 不设上限
SOURCE_OPTIONS = ("auto", "short", "arc", "events")   # 分镜素材：自动 / 短篇正文 / 小故事线 / 原始事件
SOURCE_LABEL = {"auto": "自动（有短篇正文用正文，否则用小故事线）", "short": "短篇正文（全文）",
                "arc": "小故事线（幕、结局与其来源事件）", "events": "原始事件（故事来源的世界事件全文）"}
READING_OPTIONS = ("ltr", "rtl")
COLOR_OPTIONS = ("color", "bw")
READING_LABEL = {"ltr": "从左到右", "rtl": "从右到左"}
COLOR_LABEL = {"color": "彩色", "bw": "黑白网点"}
CLOSE_SHOTS = ("近景", "特写")
SPEECH_KINDS = ("speech", "thought", "shout")
SIDES = ("左", "中", "右")
SIDE_X = {"左": 0.25, "中": 0.5, "右": 0.75}   # 说话人在画面中的大致横向位置（占格宽比例）
POS_ZONE = {
    "左上": ("top", "left"), "右上": ("top", "right"),
    "左下": ("bottom", "left"), "右下": ("bottom", "right"),
    "中上": ("top", "center"), "中下": ("bottom", "center"),
}
CANDIDATE_MAX = 3          # 每格候选数上限的默认值（世界设置可改，见 engine.comic_limits）
CANDIDATE_DEFAULT = 1      # 每次渲染默认生成的候选数
CANDIDATE_HARD_MAX = 5     # 世界设置允许的候选数上限的硬顶
CANDIDATE_RANGE = (1, CANDIDATE_HARD_MAX)
IMAGE_BUDGET_DEFAULT = 80  # 单次操作图像调用上限的默认值
IMAGE_BUDGET_RANGE = (1, 500)
RETRY_RANGE = (1, 30)      # 单次模型任务的失败重试次数（每次都把错误反馈给模型）
SHEET_KEYS = ("side", "angry", "sad", "surprised")   # 角色表：侧面 + 三种表情；平静即立绘本身
SHEET_PROMPTS = {
    "side": "side view, full body, same character design",
    "angry": "angry expression, furious, same character design",
    "sad": "sad expression, tearful, same character design",
    "surprised": "surprised expression, wide eyes, same character design",
}
NARRATION_MAX = 30         # 旁白字数上限（超过视为复述或啰嗦）
CAMERAS = ("平视", "俯视", "仰视", "过肩", "特写镜头", "推近", "拉远", "反打")
CAMERA_EN = {"平视": "eye-level shot", "俯视": "high-angle shot", "仰视": "low-angle shot",
             "过肩": "over-the-shoulder shot", "特写镜头": "close-up insert", "推近": "dolly-in shot",
             "拉远": "wide pull-back shot", "反打": "reverse shot"}
GAZES = ("朝左", "朝右", "朝画外", "朝观众", "向下", "向上", "看向对方", "低头")
GAZE_EN = {"朝左": "looking left", "朝右": "looking right", "朝画外": "looking out of frame",
           "朝观众": "looking at the viewer", "向下": "looking down", "向上": "looking up",
           "看向对方": "looking at the other character", "低头": "head bowed"}
TEMPLATE_LABEL = {"single": "整页单格", "stack": "上下排列", "grid": "标准两列", "mixed": "大小混排"}


# ---------- 选项

def normalize_opts(pages: Any = None, reading: Optional[str] = None, color: Optional[str] = None) -> Dict[str, Any]:
    """规范化用户设置。pages：整数 1–20，或 "auto"（按故事长度，不设上限）；缺省为 auto。"""
    if pages is None or pages == "":
        pages = DEFAULT_PAGES
    if isinstance(pages, str):
        s = pages.strip().lower()
        if s in ("auto", "自动"):
            pages = "auto"
        else:
            try:
                pages = int(s)
            except ValueError as exc:
                raise ValueError(f"页数无效：{pages}") from exc
    if pages != "auto":
        if isinstance(pages, bool) or not isinstance(pages, int) or not (1 <= pages <= MAX_PAGES):
            raise ValueError(f"页数须为 1–{MAX_PAGES} 或 auto：{pages}")
    reading = reading or "ltr"
    if reading not in READING_OPTIONS:
        raise ValueError(f"阅读方向无效：{reading}（可选 ltr / rtl）")
    color = color or "color"
    if color not in COLOR_OPTIONS:
        raise ValueError(f"色彩模式无效：{color}（可选 color / bw）")
    return {"pages": pages, "reading": reading, "color": color}


def expected_pages(opts: Dict[str, Any]) -> Optional[int]:
    """返回固定页数；auto 返回 None（由故事长度决定）。"""
    p = opts.get("pages")
    return None if p == "auto" else int(p)


# ---------- 版式

def _span(panel: Dict[str, Any]) -> int:
    try:
        return 2 if int(panel.get("span") or 1) >= 2 else 1
    except (TypeError, ValueError):
        return 1


def _rows(panels: List[Dict[str, Any]]) -> List[List[int]]:
    """把格子按阅读顺序分行：整行格独占一行；连续的半行格两两配对；落单的半行格占整行。"""
    rows: List[List[int]] = []
    pending: List[int] = []
    for i, p in enumerate(panels):
        if _span(p) == 2:
            if pending:
                rows.append(pending)
                pending = []
            rows.append([i])
        else:
            pending.append(i)
            if len(pending) == 2:
                rows.append(pending)
                pending = []
    if pending:
        rows.append(pending)
    if len(rows) == 1 and len(rows[0]) == 2:
        # 整页只有两个半行格：拆成上下两行整宽格，避免撑成两根窄竖条
        rows = [[rows[0][0]], [rows[0][1]]]
    return rows


def choose_template(panels: List[Dict[str, Any]]) -> str:
    n = len(panels)
    if n <= 1:
        return "single"
    spans = {_span(p) for p in panels}
    if spans == {2}:
        return "stack"
    if spans == {1}:
        return "grid"
    return "mixed"


def layout_page(panels: List[Dict[str, Any]], reading: str = "ltr") -> Dict[str, Any]:
    """返回 {"template", "rects"}；rects 为与 panels 一一对应的 (x, y, w, h)，占页面比例（不含格间距）。"""
    m = PAGE_MARGIN
    rects: List[Optional[Tuple[float, float, float, float]]] = [None] * len(panels)
    if not panels:
        return {"template": "single", "rects": []}
    rows = _rows(panels)
    weights = []
    for r in rows:
        full_hero = len(r) == 1 and _span(panels[r[0]]) == 2
        weights.append(1.0 if (full_hero or len(rows) == 1) else 0.8)
    total = sum(weights)
    avail_h = 1 - 2 * m
    avail_w = 1 - 2 * m
    y = m
    for r, wgt in zip(rows, weights):
        h = avail_h * wgt / total
        k = len(r)
        col_w = avail_w / k
        for j, idx in enumerate(r):
            col = (k - 1 - j) if reading == "rtl" else j   # 右到左阅读：同一行的第一格放在最右边
            rects[idx] = (m + col * col_w, y, col_w, h)
        y += h
    return {"template": choose_template(panels), "rects": rects}


# ---------- 排字

def _pos_zone(position: Optional[str], shot: str, side: str, k: int) -> Tuple[str, str]:
    zone = POS_ZONE.get(position or "")
    if zone is None:
        zone = ("top", "right") if side != "左" else ("top", "left")
    vert, horiz = zone
    if horiz == "center" and shot in CLOSE_SHOTS:
        # 近景/特写：画面正中是人脸，气泡让开到左或右角（按说话人方向，中间人物则交替）
        if side == "左":
            horiz = "left"
        elif side == "右":
            horiz = "right"
        else:
            horiz = "left" if k % 2 == 0 else "right"
    return vert, horiz


def plan_lettering(panel: Dict[str, Any], reading: str = "ltr") -> List[Dict[str, Any]]:
    """返回本格的排字条目：caption（旁白）、speech/thought/shout（对白）、sfx（音效）。

    每个条目含 zone=(top|bottom, left|center|right)；对白还含 tail（尾巴指向的格内相对坐标）。
    """
    shot = panel.get("shot") or "中景"
    items: List[Dict[str, Any]] = []
    cap = str(panel.get("narration") or "").strip()
    if cap:
        items.append({"kind": "caption", "text": cap, "speaker": "", "zone": ("top", "right" if reading == "rtl" else "left")})
    k = 0
    for dl in panel.get("dialogue") or []:
        if not isinstance(dl, dict):
            continue
        text = str(dl.get("text") or "").strip()
        if not text:
            continue
        side = dl.get("speaker_side") if dl.get("speaker_side") in SIDES else None
        pos_side = {"left": "左", "right": "右"}
        zone = _pos_zone(dl.get("position"), shot, side or "中", k)
        if side is None:
            side = pos_side.get(zone[1], "中")
        kind = dl.get("kind") if dl.get("kind") in SPEECH_KINDS else "speech"
        tail_y = 0.6 if zone[0] == "top" else 0.4
        items.append({
            "kind": kind, "text": text, "speaker": str(dl.get("speaker") or ""), "zone": zone,
            "tail": (SIDE_X[side], tail_y),
        })
        k += 1
    sfx = str(panel.get("sfx") or "").strip()
    if sfx:
        items.append({"kind": "sfx", "text": sfx, "speaker": "", "zone": ("bottom", "left" if reading == "rtl" else "right")})
    return items


def plan_page(page: Dict[str, Any], reading: str = "ltr") -> Dict[str, Any]:
    """整页规划：版式 + 每格的矩形与排字条目。PNG 与 HTML 共用。"""
    panels = page.get("panels") or []
    lay = layout_page(panels, reading)
    out_panels = []
    for p, rect in zip(panels, lay["rects"]):
        out_panels.append({
            "rect": list(rect),
            "items": plan_lettering(p, reading),
            "image": p.get("image"),
            "image_error": p.get("image_error"),
            "shot": p.get("shot") or "中景",
        })
    return {"no": page.get("no"), "template": lay["template"], "reading": reading, "panels": out_panels,
            "beat": page.get("beat") or "", "hook": page.get("hook") or ""}


def expression_key(emotion: Optional[str]) -> Optional[str]:
    """把分镜中的情绪词映射到角色表的表情键；平静或无法识别时返回 None（沿用立绘）。"""
    e = emotion or ""
    if any(k in e for k in ("愤", "怒", "生气", "恼")):
        return "angry"
    if any(k in e for k in ("悲", "哭", "伤", "难过", "绝望")):
        return "sad"
    if any(k in e for k in ("惊", "震", "错愕")):
        return "surprised"
    return None
