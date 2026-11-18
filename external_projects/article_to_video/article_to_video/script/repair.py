"""script/repair.py — R11：口播稿的局部修正（由 LLM 只改出问题的句子，不整章重写）。

流程：校验发现的句子级问题（数字、英文词、符号、过长、金句不匹配）交给模型，模型只返回这些句子的修正。
金句的判断交给模型：若原文中有意思相同的句子，模型给出该句的编号（source_index），
系统直接采用那一句原文作为金句，不做逐字比较。修正后仍不合格、或存在段落级错误时，回到整章重写。
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

from ..planning.jsonio import parse_json_object

REPAIR_SYSTEM = (
    "你是口播稿的修订编辑。只输出一个 JSON 对象，不要解释、前言或 Markdown 围栏。"
    "只修改被指出的句子，其余内容不动。不得添加原文没有的事实、数字或英文词。"
)

MAX_REPAIR_ROUNDS = 2
_KIND_HINT = {
    "quote": "若下面的原文句子中有意思相同的一句，source_index 填它的编号，text 留空；"
             "否则 quote 设为 false，并改写为口语句子。",
    "number": "只保留原文中出现过的数字；无法确认的数字删掉，改用文字描述。",
    "latin": "删除原文中没有的英文词，或改用中文。",
    "markdown": "去掉 Markdown 符号。",
    "formula": "公式改为口述。",
    "too_long": "缩短到 30 字以内，保持一句一个意思。",
    "empty": "补写一句与本段相关的口语句子。",
}
_SPLIT = re.compile(r"[。！？!?；;\n]+")
_CLEAN = re.compile(r"[>*`#|]+")


def source_sentences(source: str) -> List[str]:
    """原文按句切分，去掉 Markdown 标记，供模型按编号引用金句。"""
    out: List[str] = []
    for piece in _SPLIT.split(source or ""):
        t = " ".join(_CLEAN.sub("", piece).split())
        if len(t) >= 4:
            out.append(t)
    return out


def _flag_id(f: Dict[str, Any]) -> str:
    return f"{f['beat']}#{f['index']}"


def repair_prompt(flags: List[Dict[str, Any]], script: Dict[str, Any], sents: List[str]) -> str:
    current = {}
    for b in script.get("beats") or []:
        for i, s in enumerate(b.get("sentences") or []):
            current[f"{b.get('id', '?')}#{i}"] = str(s.get("text", ""))
    lines = []
    for f in flags:
        fid = _flag_id(f)
        hint = _KIND_HINT.get(f["kind"], "修正这句话，使其符合要求。")
        lines.append(f"- id: {fid}\n  问题：{f['problem']}\n  当前文字：{current.get(fid, '')}\n  处理：{hint}")
    numbered = "\n".join(f"[{i}] {t}" for i, t in enumerate(sents)) or "（无）"
    return (
        "【口播修正任务】\n"
        "下面的句子存在问题。请只返回这些句子的修正，输出如下结构的 JSON：\n"
        '{"fixes": [{"id": "P01.b01#2", "text": "修正后的句子", "quote": false, "source_index": null}]}\n'
        "source_index 只在金句匹配原文时填写（编号见【原文句子】）。每个 id 必须出现一次。\n\n"
        "【需要修正的句子】\n" + "\n".join(lines) + "\n\n"
        "【原文句子】\n" + numbered + "\n"
    )


def apply_fixes(script: Dict[str, Any], fixes: List[Dict[str, Any]], sents: List[str]) -> int:
    """把模型给出的修正写回稿件（原地修改）。返回实际修改的句子数。"""
    index: Dict[str, Dict[str, Any]] = {}
    for b in script.get("beats") or []:
        for i, s in enumerate(b.get("sentences") or []):
            index[f"{b.get('id', '?')}#{i}"] = s
    changed = 0
    for fx in fixes:
        if not isinstance(fx, dict):
            continue
        target = index.get(str(fx.get("id", "")))
        if target is None:
            continue
        src_i = fx.get("source_index")
        if isinstance(src_i, int) and 0 <= src_i < len(sents):
            target["text"] = sents[src_i]          # 金句：采用模型指出的原文句子
            target["quote"] = True
            changed += 1
        elif str(fx.get("text", "")).strip():
            target["text"] = str(fx["text"]).strip()
            if "quote" in fx:
                target["quote"] = bool(fx["quote"])
            changed += 1
    return changed


def repair_script(llm, script: Dict[str, Any], flags: List[Dict[str, Any]], source: str,
                  ask_cancel=None) -> Optional[int]:
    """对句子级问题做一轮局部修正。返回修改的句子数；模型输出不是合法 JSON 时返回 None（由调用方改为整章重写）。"""
    sents = source_sentences(source)
    raw = llm.ask(repair_prompt(flags, script, sents), system=REPAIR_SYSTEM)
    if ask_cancel:
        ask_cancel()
    try:
        obj = parse_json_object(raw)
    except ValueError:
        return None
    fixes = obj.get("fixes") if isinstance(obj, dict) else None
    if not isinstance(fixes, list):
        return None
    return apply_fixes(script, fixes, sents)
