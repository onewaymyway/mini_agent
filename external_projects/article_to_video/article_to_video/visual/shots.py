"""visual/shots.py — 模型版分镜的镜头结构（第一期）：句子编号、分窗、逐步出现时机（纯函数）。

计划文档：next_doc/article_to_video_storyboard_model_redesign_plan.md 第 4 节。

数据约定：
  * 句子编号 sentence_id = "<段号>.sNN"（与 timeline.json 的句 ID 一致）。
  * 镜头 = 同一段内一段连续句子，sentences = [首句编号, 末句编号]。
  * 镜头在内部仍是场景（scene）形状：type / items / reveal 与渲染器保持不变；
    reveal.at_sentence 仍是段内句序号（从 0 起），渲染器据此对齐到时间轴。
  * 没有 sentences 字段的旧场景（规则版）视为覆盖整段。
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..validators.fidelity import norm_key
from .states import uniform_reveal

WINDOW_MAX_CHARS = 2500            # 分窗上限（字）：段落边界切分，单段超过上限时自成一窗
ROLES = ("argument", "data", "structure", "formula", "quote", "narrative", "image", "hold")
SHOT_KINDS = ("keypoint", "illustration", "quote", "formula", "flow", "stack", "chart", "table", "list",
              "stages", "definition", "contrast", "recap", "hold")
KIND_TO_TYPE = {"keypoint": "keypoint", "illustration": "illustration", "quote": "quote",
                "formula": "formula", "flow": "diagram", "stack": "stack", "chart": "table", "table": "table",
                "list": "list", "stages": "stages", "definition": "definition", "contrast": "contrast",
                "recap": "recap"}
TYPE_TO_KIND = {"keypoint": "keypoint", "illustration": "illustration", "quote": "quote",
                "formula": "formula", "diagram": "flow", "stack": "stack", "table": "table", "list": "list",
                "timeline": "list", "stages": "stages", "definition": "definition", "contrast": "contrast",
                "recap": "recap"}
MAX_SHOT_ITEMS = 3                 # 单镜元素上限（计划 4.5）；流程、表格、原文列表由原文结构决定，不受此限
POINT_LIMIT = 20                   # 视觉要点：每条字数上限（计划 4.1）
MAX_POINTS = 4                     # 视觉要点：每段条数上限


def sentence_id(beat_id: str, index: int) -> str:
    return f"{beat_id}.s{index + 1:02d}"


def sentence_index(sid: str) -> int:
    """句子编号 → 段内序号（从 0 起）。"""
    return int(sid.rsplit(".s", 1)[1]) - 1


def beat_of(sid: str) -> str:
    return sid.rsplit(".s", 1)[0]


def beat_sentence_ids(beat: Dict[str, Any]) -> List[str]:
    return [sentence_id(beat["id"], i) for i in range(len(beat["sentences"]))]


def beat_chars(beat: Dict[str, Any]) -> int:
    return sum(len(s["text"]) for s in beat["sentences"])


def plan_windows(beats: Sequence[Dict[str, Any]], max_chars: int = WINDOW_MAX_CHARS) -> List[List[Dict[str, Any]]]:
    """按段落边界把章切成若干窗口：累计字数超过 max_chars 时另起一窗（单段超限则自成一窗）。"""
    windows: List[List[Dict[str, Any]]] = []
    cur: List[Dict[str, Any]] = []
    total = 0
    for beat in beats:
        n = beat_chars(beat)
        if cur and total + n > max_chars:
            windows.append(cur)
            cur, total = [], 0
        cur.append(beat)
        total += n
    if cur:
        windows.append(cur)
    return windows


def shot_range(sc: Dict[str, Any], beat_sentence_count: int) -> Tuple[int, int]:
    """场景覆盖的段内句序号 [a, b]（含两端）。没有 sentences 的旧场景覆盖整段。"""
    rng = sc.get("sentences")
    if isinstance(rng, (list, tuple)) and len(rng) == 2 and rng[0] and rng[1]:
        return sentence_index(str(rng[0])), sentence_index(str(rng[1]))
    return 0, max(0, beat_sentence_count - 1)


def shot_edge_ids(sc: Dict[str, Any], beat_id: str, beat_sentence_count: int) -> Tuple[str, str]:
    a, b = shot_range(sc, beat_sentence_count)
    return sentence_id(beat_id, a), sentence_id(beat_id, b)


def mention(key_text: str, texts: Sequence[str]) -> bool:
    """key_text（归一化后）是否出现在任一句子中。"""
    key = norm_key(key_text)
    return bool(key) and any(key in norm_key(t) for t in texts)


def first_mention(key_text: str, texts: Sequence[str]) -> Optional[int]:
    key = norm_key(key_text)
    if not key:
        return None
    for j, t in enumerate(texts):
        if key in norm_key(t):
            return j
    return None


def scene_reveal(sc: Dict[str, Any], texts: Sequence[str], start: int) -> List[Dict[str, int]]:
    """逐步出现的时机（段内句序号）。

    * diagram（流程）：第 i 个状态显示前 i+1 个节点，节点在句子中首次被提及时出现；
    * 其它：状态均匀分布在镜头的句子上。
    texts 为镜头覆盖的句子（按顺序），start 为镜头首句在段内的序号。
    """
    n = max(1, len(texts))
    if sc.get("type") == "diagram" and sc.get("items"):
        out: List[Dict[str, int]] = []
        prev = 0
        for i, item in enumerate(sc["items"]):
            at = first_mention(str(item), texts)
            at = prev if at is None else max(prev, at)
            out.append({"step": i, "at_sentence": start + at})
            prev = at
        return out
    base = uniform_reveal(sc, n)
    return [{"step": r["step"], "at_sentence": start + r["at_sentence"]} for r in base]


def reveal_in_range(sc: Dict[str, Any], a: int, b: int) -> bool:
    """逐步出现的时机是否都在镜头句子范围内，且不倒退（用于校验与编辑保存）。"""
    from .states import expected_state_count

    reveal = sc.get("reveal") or []
    if len(reveal) != expected_state_count(sc):
        return False
    prev = a - 1
    for i, r in enumerate(reveal):
        k = r.get("at_sentence") if isinstance(r, dict) else None
        if not isinstance(k, int) or k < a or k > b or k < prev or r.get("step") != i:
            return False
        prev = k
    return True
