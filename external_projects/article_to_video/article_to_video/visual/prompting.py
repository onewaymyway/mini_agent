"""visual/prompting.py — 第三期：插图的内容决定与提示词结构（计划第 6 节）。

插图提示词由四部分组成：视觉主体（具体物体）+ 场景（所处环境）+ 构图（画面安排）+ 风格锚，
并固定附加“画面中不出现任何文字、字母、数字、标题、标志或水印”的约束。

检查（纯函数）：
  * 视觉主体必须是具体的物体或场景，不得是抽象词（如“效率”“趋势”）；
  * 主体、场景、构图中不得出现文字类请求（FORBIDDEN_WORDS）；
  * 插图只用于比喻、场景与抽象概念的具体化；数据、流程、定义应使用图表、流程或卡片（由落镜规则约束）。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from .config import FORBIDDEN_WORDS, aspect_ratio

PROMPT_BUILD_VERSION = "p4-img-v2"   # v2（第三期）：主体 + 场景 + 构图 + 风格锚 + 无文字约束
NO_TEXT = "画面中不出现任何文字、字母、数字、标题、标志或水印"
DEFAULT_COMPOSITION = "主体位于画面中部，四周留有空白"
MAX_SUBJECT = 24
MAX_SCENE = 40
MAX_COMPOSITION = 40

# 常见抽象词：作为视觉主体时无法画成具体画面。只做明显情形的拦截，不求穷尽。
ABSTRACT_WORDS = frozenset({
    "效率", "价值", "趋势", "增长", "发展", "变革", "未来", "机会", "风险", "能力", "逻辑", "理念",
    "思想", "概念", "模式", "体系", "力量", "关系", "本质", "结构", "过程", "问题", "方向", "时代",
    "成本", "收益", "规模", "速度", "水平", "压力", "动力", "阶段", "周期", "环境", "影响", "作用",
})
_SPLIT = re.compile(r"[、，,；;和与及/\s]+")


def is_abstract_subject(subject: str) -> bool:
    """主体整体是抽象词，或其全部组成词都是抽象词。"""
    s = (subject or "").strip()
    if not s:
        return False
    if s in ABSTRACT_WORDS:
        return True
    parts = [p for p in _SPLIT.split(s) if p]
    return bool(parts) and all(p in ABSTRACT_WORDS for p in parts)


def check_illustration(image: Dict[str, Any]) -> List[str]:
    """插图字段的检查，返回错误说明（空表示通过）。image 含 subject / scene / composition。"""
    errs: List[str] = []
    subject = str(image.get("subject") or "").strip()
    scene = str(image.get("scene") or "").strip()
    comp = str(image.get("composition") or "").strip()
    if not subject:
        errs.append("插图缺少视觉主体")
    elif is_abstract_subject(subject):
        errs.append(f"插图主体「{subject}」是抽象词，应改为具体的物体或场景")
    for label, text in (("视觉主体", subject), ("场景", scene), ("构图", comp)):
        for w in FORBIDDEN_WORDS:
            if w and w in text:
                errs.append(f"插图{label}含禁用词「{w}」（插图禁止文字类内容）")
                break
    return errs


def compose_prompt(image: Dict[str, Any], style_anchor: str, orientation: str) -> str:
    """组装生图提示词：主体、场景、构图、风格锚、无文字约束、画幅。"""
    subject = str(image.get("subject") or "").strip()
    scene = str(image.get("scene") or "").strip()
    comp = str(image.get("composition") or "").strip() or DEFAULT_COMPOSITION
    head = subject + (f"，置身于{scene}" if scene else "")
    return f"{head}。构图：{comp}。{style_anchor}。{NO_TEXT}。画幅比例{aspect_ratio(orientation)}。"
