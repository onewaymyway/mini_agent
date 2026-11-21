"""deck/background.py — 新方案页面背景插图（R34）。

规则（见 next_doc/article_to_video_external_project_plan.md 第 45.7 节）：
- 按「节」生成背景：同一节的所有页共用一张图，页与页之间用不同取景与压暗度区分；
- 默认开启（deck_background.enabled），同时需要「启用 AI 插图」（visual.images.enabled）；
  没有生图服务（未配置 key）时不生图，页面使用原来的渐变背景；
- 提示词：文本模型根据节标题与本节内容写一个具体的画面主体（失败则用固定的抽象主体），
  再套用风格锚与「画面中不出现任何文字」约束（复用 visual/prompting.py）；
- 缓存：按输入（节标题、内容、风格锚、画幅、尺寸、版本）计算指纹，输入未变则复用，
  不调用模型也不生图；改排版、压暗度不会触发重新生图；
- 失败：单节生图失败只记录原因，该节回退渐变；返回“不可重试”的配置错误（如鉴权失败）时，
  本章后续各节不再尝试。停止请求（Cancelled）原样上抛。
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path
from typing import Any, Dict, List, Optional

from .. import cancel, joblog
from ..project_store import read_json, write_json_atomic
from ..visual.config import aspect_ratio
from ..visual.prompting import check_illustration, compose_prompt
from .store import deck_root

BG_VERSION = "deck-bg-v1"
BG_COMPOSITION = "主体偏向画面一侧，中部与文字区域保持简洁、留白"
FALLBACK_SUBJECT = "抽象的柔和光线与有机纹理，层次分明"
SYSTEM = "你是讲解视频的视觉设计师，只输出一行画面主体描述，不解释，不加引号。"
SUBJECT_MAX_CHARS = 30
CONTEXT_MAX_CHARS = 300
# 同一节内第 j 页的取景与压暗偏移（循环使用）
FOCUS_SEQUENCE = (0.5, 0.2, 0.8, 0.35, 0.65)
DARKEN_STEPS = (0.0, 0.06, -0.04, 0.03, -0.02)
DARKEN_MIN, DARKEN_MAX = 0.45, 0.7
DEFAULT_DARKEN = 0.55


# ---------------- 分节 ----------------

def section_key(page: Dict[str, Any], slide: Dict[str, Any]) -> str:
    """节键：页所属的第一个原文小节；没有小节信息时退回页号。"""
    ids = slide.get("section_ids") or []
    return str(ids[0]) if ids else f"page:{page.get('id')}"


def _items_text(el: Dict[str, Any]) -> str:
    if isinstance(el.get("items"), list):
        return "；".join(str(x) for x in el["items"] if isinstance(x, str))
    if isinstance(el.get("rows"), list):
        return "；".join("、".join(str(c) for c in r) for r in el["rows"] if isinstance(r, list))
    return str(el.get("text") or "")


def plan_sections(pages: List[Dict[str, Any]], slides: Dict[str, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """按出现顺序把页分成节：返回 [{key, title, context, pages}]。
    title 取节内第一页的页标题；context 取节内各页标题与前两个元素的文字（用于写提示词）。"""
    order: List[str] = []
    groups: Dict[str, List[Dict[str, Any]]] = {}
    for page in pages:
        slide = slides.get(page["id"], {})
        key = section_key(page, slide)
        if key not in groups:
            groups[key] = []
            order.append(key)
        groups[key].append(page)
    out: List[Dict[str, Any]] = []
    for key in order:
        sec_pages = groups[key]
        bits: List[str] = []
        for page in sec_pages:
            slide = slides.get(page["id"], {})
            bits.append(str(slide.get("title") or page.get("title") or ""))
            for el in (slide.get("elements") or [])[:2]:
                bits.append(_items_text(el))
        first = slides.get(sec_pages[0]["id"], {})
        title = str(first.get("title") or sec_pages[0].get("title") or key)
        context = " ".join(b for b in bits if b).strip()[:CONTEXT_MAX_CHARS]
        out.append({"key": key, "title": title, "context": context, "pages": sec_pages})
    return out


def page_background_params(j: int, base_darken: float) -> tuple:
    """同一节第 j 页的（取景 0..1，压暗度）。压暗度限制在可读范围内。"""
    focus = FOCUS_SEQUENCE[j % len(FOCUS_SEQUENCE)]
    dark = base_darken + DARKEN_STEPS[j % len(DARKEN_STEPS)]
    return focus, min(DARKEN_MAX, max(DARKEN_MIN, dark))


# ---------------- 主体与提示词 ----------------

def clean_subject(raw: Any) -> Optional[str]:
    """清洗模型输出的主体：取第一行，去掉引号与前缀；不合格（抽象、含文字类词、过长、为空）返回 None。"""
    text = str(raw or "").strip()
    if not text:
        return None
    s = text.splitlines()[0].strip().strip("「」\"'“”。.；;：: ")
    m = re.match(r"^(画面)?主体[：:]\s*(.+)$", s)
    if m:
        s = m.group(2).strip().strip("「」\"'“”。.；;")
    if not s or len(s) > SUBJECT_MAX_CHARS:
        return None
    if check_illustration({"subject": s, "scene": "", "composition": ""}):
        return None
    return s


def build_subject(title: str, context: str, llm) -> str:
    """节的画面主体：有模型则请模型写一个具体的主体；模型失败或输出不合格则用固定的抽象主体。"""
    if llm is None:
        return FALLBACK_SUBJECT
    prompt = (
        "请为下面这一节的讲解视频写一张背景插图的画面主体：一个具体的物体、场景或自然景象，"
        "与内容有联系，但不要抽象概念，画面中不能有任何文字。只输出一行，不超过 30 个字。\n"
        f"小节标题：{title}\n小节内容：{context or '（无）'}"
    )
    try:
        raw = llm.ask(prompt, system=SYSTEM)
    except Exception as exc:  # noqa: BLE001 - 模型失败只影响主体，回退固定主体，不中断
        joblog.bound_log(f"背景主体：模型调用失败（{exc}），改用通用主体", level="warn")
        return FALLBACK_SUBJECT
    subject = clean_subject(raw)
    if subject is None:
        joblog.bound_log(f"背景主体：模型输出不合格，「{title}」改用通用主体", level="warn")
        return FALLBACK_SUBJECT
    return subject


def background_sha(title: str, context: str, style_anchor: str, orientation: str, size: str) -> str:
    raw = "|".join([BG_VERSION, title, context, style_anchor, orientation, size])
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _safe(key: str) -> str:
    return re.sub(r"[^0-9A-Za-z_\-]+", "_", key) or "sec"


def backgrounds_dir(pdir: Path, part_id: str) -> Path:
    return deck_root(Path(pdir), part_id) / "backgrounds"


def load_background_index(pdir: Path, part_id: str) -> Dict[str, Any]:
    p = backgrounds_dir(pdir, part_id) / "index.json"
    return read_json(p) if p.is_file() else {}


# ---------------- 生成（带缓存） ----------------

def ensure_section_backgrounds(pdir: Path, part_id: str, sections: List[Dict[str, Any]], *,
                               provider, style_anchor: str, orientation: str, size: str,
                               llm=None) -> Dict[str, Optional[Path]]:
    """为每一节返回背景图路径（None 表示使用渐变背景）。

    顺序处理，每节先查缓存：输入未变且文件存在则复用；否则在有生图服务时生成。
    provider 为 None（未配置 key）时不生图，但仍复用已有的缓存图。"""
    bdir = backgrounds_dir(Path(pdir), part_id)
    index_path = bdir / "index.json"
    index = load_background_index(Path(pdir), part_id)
    result: Dict[str, Optional[Path]] = {}
    disabled = False
    if provider is None:
        joblog.bound_log(f"{part_id}：未配置生图服务（缺少 API key 或已关闭），背景使用渐变色")
    for sec in sections:
        key, title, context = sec["key"], sec["title"], sec["context"]
        sha = background_sha(title, context, style_anchor, orientation, size)
        entry = index.get(key)
        if entry and entry.get("sha") == sha and Path(entry.get("file", "")).is_file():
            joblog.bound_log(f"{part_id} 背景「{title}」：复用已生成的图片（输入未变）")
            result[key] = Path(entry["file"])
            continue
        if provider is None or disabled:
            result[key] = None
            continue
        cancel.check()
        subject = build_subject(title, context, llm)
        prompt = compose_prompt({"subject": subject, "scene": "", "composition": BG_COMPOSITION},
                                style_anchor, orientation)
        version = int(entry.get("version", 0)) + 1 if entry else 1
        bdir.mkdir(parents=True, exist_ok=True)
        out = bdir / f"{_safe(key)}_v{version:03d}.png"
        joblog.bound_log(f"{part_id} 背景「{title}」：调用生图（第 {version} 版）：{subject}")
        res = provider.generate(prompt, out, ratio=aspect_ratio(orientation), size=size)
        if not res.ok:
            joblog.bound_log(f"{part_id} 背景「{title}」：生图失败，本节改用渐变色：{res.error or '未知原因'}",
                             level="warn")
            if res.non_retryable:
                disabled = True
                joblog.bound_log(f"{part_id}：生图请求被拒绝（配置问题），本章其余各节不再生图", level="warn")
            result[key] = None
            continue
        index[key] = {"sha": sha, "version": version, "file": str(out), "subject": subject, "prompt": prompt}
        write_json_atomic(index_path, index)
        joblog.bound_log(f"{part_id} 背景「{title}」：已生成 {out.name}", level="ok")
        result[key] = out
    return result
