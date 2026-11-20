"""visual/states.py — 场景的“逐步出现”状态（分镜、校验、渲染共用，只在这里定义一次）。

  * 普通场景（keypoint / illustration / quote / list 之外的卡片）：第 i 个状态显示前 i+1 个条目；
    无条目时只有一个状态（空）。
  * 结构化场景（ALL_AT_ONCE_TYPES：流程、堆叠层图、表格、列表、年代线、递进卡、定义卡）：整体一次显示，只有一个状态。
    第一期起（分镜改进）结构不再一块一块出现；需要的话由镜头分页（续镜）控制篇幅。
  * formula 公式场景：状态 0 只显示公式，状态 k 显示前 k 条解释（公式本体始终可见）。

reveal 列表与状态一一对应：reveal[i] = {"step": i, "at_sentence": 句序号}。
"""

from __future__ import annotations

from typing import Any, Dict, List

SCENE_TYPES = ("keypoint", "list", "timeline", "diagram", "stack", "table", "quote", "formula", "illustration",
               "stages", "definition")
STRUCTURED_TYPES = ("diagram", "stack", "table", "list", "timeline", "formula", "stages", "definition")
ALL_AT_ONCE_TYPES = ("diagram", "stack", "table", "list", "timeline", "stages", "definition")


def visible_counts(scene: Dict[str, Any]) -> List[int]:
    n = len(scene.get("items") or [])
    kind = scene.get("type")
    if kind == "formula":
        return list(range(0, n + 1))
    if kind in ALL_AT_ONCE_TYPES:
        return [n] if n else [0]
    if n == 0:
        return [0]
    return list(range(1, n + 1))


def expected_state_count(scene: Dict[str, Any]) -> int:
    return len(visible_counts(scene))


def uniform_reveal(scene: Dict[str, Any], sentence_count: int) -> List[Dict[str, int]]:
    """把各状态均匀分布到句子上（用于编辑后重算，或没有句级信息时）。"""
    states = expected_state_count(scene)
    s = max(1, int(sentence_count))
    n_items = len(scene.get("items") or [])
    out: List[Dict[str, int]] = []
    for i in range(states):
        if scene.get("type") == "formula":
            k = 0 if i == 0 else int((i - 1) * s / max(1, n_items))
        else:
            k = int(i * s / max(1, states))
        out.append({"step": i, "at_sentence": min(s - 1, max(0, k))})
    return out
