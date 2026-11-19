"""script/structure.py — 第二期：含结构段落的口播（计划第 5 节）。

含结构段落 = 段落引用的原文块中有流程（chain）、表格（table）或公式（math）。对这类段落：
  * 口播不得依赖画面（“如图所示”“见下图”“上面的流程图”等）——这是校验错误，可局部修正；
  * 流程节点应在口播中被提及（缺失记为警告，修订时作为必须项）；
  * 表格中的数字应在口播中被提及（缺失记为警告，只报告）。
纯函数，不调用模型。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Sequence, Tuple

from ..validators.fidelity import number_tokens
from ..visual.director import _refs_blocks, plain
from ..visual.shots import mention

VISUAL_REF = re.compile(r"如图|见图|图中|图示|见下图|见上图|如下图|如上图|上图|下图|上面的图|下面的图|"
                        r"流程图|如下表|如上表|见下表|见上表|表格所示|见表")


def beat_structure(beat: Dict[str, Any], blocks: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """段落的原文结构：kinds（chain / table / formula）、流程节点、表格中的数字。"""
    kinds: List[str] = []
    nodes: List[str] = []
    numbers: List[str] = []
    for b in _refs_blocks(beat.get("source_refs") or [], list(blocks)):
        if b.get("kind") == "code" and b.get("shape") == "chain":
            kinds.append("chain")
            nodes += [plain(n) for n in b.get("nodes") or [] if plain(n)]
        elif b.get("kind") == "table":
            kinds.append("table")
            for row in b.get("rows") or []:
                for cell in row or []:
                    numbers += number_tokens(plain(cell))
        elif b.get("kind") == "math":
            kinds.append("formula")
    return {"kinds": kinds, "nodes": list(dict.fromkeys(nodes)), "numbers": list(dict.fromkeys(numbers))}


def beat_text(beat: Dict[str, Any]) -> str:
    return "".join(str(s.get("text", "")) for s in beat.get("sentences") or [])


def visual_ref_hits(beat: Dict[str, Any]) -> List[Tuple[int, str]]:
    """[(句序号, 句子)]：含依赖画面说法的句子。"""
    out = []
    for i, s in enumerate(beat.get("sentences") or []):
        t = str(s.get("text", ""))
        m = VISUAL_REF.search(t)
        if m:
            out.append((i, m.group(0)))
    return out


def missing_nodes(beat: Dict[str, Any], st: Dict[str, Any]) -> List[str]:
    text = beat_text(beat)
    return [n for n in st["nodes"] if not mention(n, [text])]


def missing_numbers(beat: Dict[str, Any], st: Dict[str, Any]) -> List[str]:
    said = set(number_tokens(beat_text(beat)))
    return [x for x in st["numbers"] if x not in said]


def structure_issues(beat: Dict[str, Any], blocks: Sequence[Dict[str, Any]]) -> List[str]:
    """需要修订的问题（触发修订）：依赖画面的说法、流程节点未提及。返回说明列表；空表示无需修订。"""
    st = beat_structure(beat, blocks)
    if not st["kinds"]:
        return []
    issues: List[str] = []
    hits = visual_ref_hits(beat)
    if hits:
        issues.append("以下句子依赖画面（应改为用口语讲清楚）：" + "；".join(f"第 {i + 1} 句含「{w}」" for i, w in hits))
    miss = missing_nodes(beat, st)
    if miss:
        issues.append("流程节点必须在口播中提及：" + "、".join(miss))
    return issues


def structure_warnings(beat: Dict[str, Any], blocks: Sequence[Dict[str, Any]]) -> List[str]:
    """只提示、不阻止的问题：表格数字未提及。"""
    st = beat_structure(beat, blocks)
    miss = missing_numbers(beat, st) if st["kinds"] else []
    if not miss:
        return []
    return [f"{beat['id']} 原文表格中的数字未在口播中提及：{'、'.join(miss[:6])}"]


def structure_report(script: Dict[str, Any], blocks: Sequence[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """第四期评估前的样例报告：每个含结构段落的节点覆盖、数字覆盖与依赖画面句子数。"""
    rows = []
    for beat in script.get("beats") or []:
        st = beat_structure(beat, blocks)
        if not st["kinds"]:
            continue
        rows.append({
            "beat": beat["id"],
            "kinds": st["kinds"],
            "nodes_total": len(st["nodes"]),
            "nodes_missing": missing_nodes(beat, st),
            "numbers_total": len(st["numbers"]),
            "numbers_missing": missing_numbers(beat, st),
            "visual_refs": len(visual_ref_hits(beat)),
        })
    return rows
