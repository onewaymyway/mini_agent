"""幻灯片规划的模型修复（R28）：确定性分页经多次细分仍不合格时，把具体问题交给文本模型修复，持续重试。

流程：
  1. 把本章原文块、当前的幻灯片规划、校验给出的具体错误一起交给模型，要求输出修正后的完整幻灯片列表；
  2. 程序把模型输出规整成规划（重新编号、统计字数与时长、前言标记由原文决定，不信任模型自报），再用同一套校验检查；
  3. 仍有错误：带着“最新一版规划 + 新的具体错误”再让模型改，直到通过或用完 REPAIR_ATTEMPTS 次（可随时停止）。
校验标准不放宽：内容必须出自原文、原文内容块全部被承载、页面不只有标题或前言、前言不留页尾、条目不超上限。
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Tuple

from .. import cancel, joblog
from ..planning.errors import PlanError
from ..planning.jsonio import parse_json_object
from .plan import _finish, _slide, _text, is_lead_in
from .validate import ELEMENT_KINDS, validate_deck

REPAIR_ATTEMPTS = 30          # 模型修复的最多次数（安全上限；用户可随时停止）
REPAIR_CAPS = (12, 12)        # 修复结果按此上限校验（页上限，元素上限）；提示中建议每页不超过 8 条
SYSTEM = ("你是讲解视频的幻灯片编排员。只输出一个 JSON 对象，不要任何解释、前言或 Markdown 围栏。"
          "幻灯片中的每一条文字必须原样取自给定的原文块，不得改写、概括、新增或编造。")


def lead_block_ids(blocks: List[Dict[str, Any]]) -> set:
    """原文中属于“前言/小标题”的块：这类内容必须与它引出的内容同页，不能单独成页或留在页尾。"""
    out = set()
    for b in blocks:
        if b.get("kind") == "heading" and (b.get("level", 2) or 2) >= 3:
            out.add(b.get("id"))
        elif is_lead_in(b):
            out.add(b.get("id"))
    return out


def _block_line(b: Dict[str, Any], lead_ids: set) -> List[str]:
    k, bid = b.get("kind"), b.get("id")
    tag = "（前言/小标题，必须与后面引出的内容同页）" if bid in lead_ids else ""
    if k == "heading":
        return [f"[{bid}] {'一二三四五六'[min((b.get('level', 2) or 2), 6) - 1]}级标题{tag}：{_text(b)}"]
    if k == "list":
        return [f"[{bid}] 列表："] + [f"    · {x}" for x in b.get("items") or []]
    if k == "table":
        return [f"[{bid}] 表格：表头 {json.dumps(b.get('header') or [], ensure_ascii=False)}"] + \
               [f"    行 {json.dumps(r, ensure_ascii=False)}" for r in b.get("rows") or []]
    if k == "code":
        nodes = b.get("nodes") or b.get("items") or []
        if b.get("shape") == "chain" and nodes:
            return [f"[{bid}] 流程：" + " → ".join(str(x) for x in nodes)]
        return [f"[{bid}] 代码/图示：" + " | ".join(str(x) for x in (nodes or [b.get("content", "")]))]
    if k == "quote":
        return [f"[{bid}] 引语：{_text(b)}"]
    if k == "rule":
        return []
    return [f"[{bid}] 段落{tag}：{_text(b)}"]


def _deck_view(deck: Dict[str, Any]) -> List[Dict[str, Any]]:
    view = []
    for s in deck.get("slides") or []:
        els = []
        for e in s.get("elements") or []:
            d = {"kind": e.get("kind"), "source": e.get("source")}
            if e.get("kind") == "table":
                d.update(header=e.get("header"), rows=e.get("rows"))
            elif e.get("kind") == "formula":
                d["text"] = e.get("text")
            else:
                d["items"] = e.get("items")
            els.append(d)
        view.append({"id": s.get("id"), "title": s.get("title"), "elements": els})
    return view


def build_repair_prompt(part_title: str, blocks: List[Dict[str, Any]], deck: Optional[Dict[str, Any]],
                        errors: List[str], page_cap: int, element_cap: int) -> str:
    lead_ids = lead_block_ids(blocks)
    lines = [f"章标题：{part_title}",
             "下面是本章的原文块和当前的幻灯片规划。当前规划没有通过校验，请按“具体问题”修正，输出完整的新规划。", "",
             "分页标准（必须全部满足）：",
             "1. 一、二级标题开始新的一页，同一页不混入两个节；",
             f"2. 每页条目总数不超过 {page_cap}（建议不超过 8），一个元素的条目不超过 {element_cap}；",
             "3. 标记为“前言/小标题”的内容必须与它引出的正文放在同一页，且排在正文之前；"
             "页面不能只有标题或前言，前言也不能排在页面最后；",
             "4. 原文中每一个有内容的块（段落、列表、表格、流程、公式、引语）都必须被某一页承载，不得遗漏；",
             "5. 幻灯片里的每条文字必须原样取自它所引用的原文块（段落可按句拆成多条），不得改写、概括或新增；",
             "6. 内容太多时从逻辑段落之间断开，续页的标题加“（续）”；表格超长按行拆分并保留表头；",
             "7. 每页元素个数尽量不超过 6 个（元素太多文字会很小，应拆成多页，续页标题加“（续）”）；",
             "8. 每个元素用 source 标明出自哪个原文块的编号。", "",
             "元素类型：keypoint（要点，items 为文字条目）、list（列表）、quote（金句，1 条）、"
             "diagram（流程，items 为节点）、formula（公式，用 text）、table（表格，用 header 与 rows）。", "",
             "原文块（按顺序）："]
    for b in blocks:
        lines += _block_line(b, lead_ids)
    lines += ["", "当前规划（有问题）：", json.dumps(_deck_view(deck), ensure_ascii=False) if deck else "（没有可用的规划，请从原文重新编排）",
              "", "具体问题（必须全部解决）："]
    lines += [f"- {e}" for e in errors[:30]]
    if len(errors) > 30:
        lines.append(f"- 另有 {len(errors) - 30} 处类似问题")
    example = {"slides": [{"title": "页标题", "elements": [
        {"kind": "keypoint", "source": "原文块编号", "items": ["原文中的一句话"]}]}]}
    lines += ["", "输出 JSON（每一页都要输出，包括没有问题的页；不要输出页编号）：",
              json.dumps(example, ensure_ascii=False)]
    return "\n".join(lines)


def normalize_repaired(obj: Any, blocks: List[Dict[str, Any]], part_id: str, page_cap: int,
                       element_cap: int) -> Tuple[Optional[Dict[str, Any]], List[str]]:
    """把模型输出规整成规划。返回 (规划或 None, 结构问题列表)。前言标记由原文决定。"""
    problems: List[str] = []
    raw = obj.get("slides") if isinstance(obj, dict) else None
    if not isinstance(raw, list) or not raw:
        return None, ["输出里没有 slides 列表，请输出 {\"slides\": [...]}"]
    by_id = {b.get("id"): b for b in blocks}
    lead_ids = lead_block_ids(blocks)
    slides: List[Dict[str, Any]] = []
    for i, rs in enumerate(raw, 1):
        label = f"第 {i} 页"
        if not isinstance(rs, dict):
            problems.append(f"{label}：不是对象")
            continue
        els: List[Dict[str, Any]] = []
        for rel in rs.get("elements") or []:
            if not isinstance(rel, dict):
                continue
            kind = str(rel.get("kind") or "")
            src = rel.get("source")
            if kind not in ELEMENT_KINDS:
                problems.append(f"{label}：元素类型 {kind or '空'} 不合法（可用 keypoint/list/quote/diagram/formula/table）")
                continue
            if src not in by_id:
                problems.append(f"{label}：元素的 source「{src}」不是本章的原文块编号")
                continue
            el: Dict[str, Any] = {"kind": kind, "items": [], "emphasis": [], "source": src,
                                  "lead_in": src in lead_ids}
            if kind == "table":
                el.update(header=[str(x) for x in rel.get("header") or []],
                          rows=[[str(c) for c in r] for r in rel.get("rows") or [] if isinstance(r, list)])
            elif kind == "formula":
                el["text"] = str(rel.get("text") or "").strip()
            else:
                el["items"] = [str(x).strip() for x in rel.get("items") or [] if str(x).strip()]
                if kind == "diagram":
                    nodes = by_id[src].get("nodes") or []
                    el["loop"] = bool(by_id[src].get("loop")) and len(nodes) == len(el["items"])
            els.append(el)
        sl = _slide("", str(rs.get("title") or "").strip(), [])
        sl["elements"] = els
        sl["source_blocks"] = list(dict.fromkeys(e["source"] for e in els))
        secs = [by_id[b].get("section") for b in sl["source_blocks"]]
        sl["_sections"] = secs or [None]
        slides.append(sl)
    if not slides:
        return None, problems or ["没有可用的幻灯片"]
    finished = [_finish(s) for s in slides]
    for i, s in enumerate(finished, 1):
        s["id"] = f"{part_id}.S{i:02d}"
    deck = {"schema": 1, "part": part_id, "mode": "deck", "slides": finished,
            "total_est_sec": round(sum(s["est_sec"] for s in finished), 1),
            "page_cap": page_cap, "element_cap": element_cap}
    return deck, problems


def repair_deck(part: Dict[str, Any], blocks: List[Dict[str, Any]], part_id: str, base: Optional[Dict[str, Any]],
                errors: List[str], llm, *, max_attempts: int = REPAIR_ATTEMPTS) -> Dict[str, Any]:
    """让模型修复不合格的规划，直到通过校验。用完次数仍不合格才抛 PlanError（附最后一次的具体问题）。"""
    if llm is None:
        raise PlanError("幻灯片规划不合格且未配置文本模型，无法自动修复")
    pc, ec = REPAIR_CAPS
    title = part.get("title", part_id)
    current, cur_errors = base, list(errors)
    for attempt in range(1, max_attempts + 1):
        cancel.check()
        joblog.bound_log(f"{part_id} 幻灯片规划交给模型修复（第 {attempt} 次）：{len(cur_errors)} 处问题", level="warn")
        for e in cur_errors[:8]:
            joblog.bound_log(f"    · {e}", level="warn")
        prompt = build_repair_prompt(title, blocks, current, cur_errors, pc, ec)
        try:
            raw = llm.ask(prompt, system=SYSTEM)
        except Exception as exc:                        # 网络/限流等：记录后继续重试，不中断
            joblog.bound_log(f"{part_id} 模型调用失败（{exc}），重试", level="warn")
            cur_errors = cur_errors or [f"模型调用失败：{exc}"]
            continue
        try:
            obj = parse_json_object(raw)
        except ValueError as exc:
            joblog.bound_log(f"{part_id} 修复第 {attempt} 次：输出不是有效 JSON（{exc}），重做", level="warn")
            cur_errors = ["上一次输出不是有效的 JSON，请只输出 JSON 对象"] + [e for e in cur_errors
                                                                      if "JSON" not in e]
            continue
        deck, structural = normalize_repaired(obj, blocks, part_id, pc, ec)
        if deck is None:
            cur_errors = structural
            continue
        v = validate_deck(deck, blocks, page_cap=pc, element_cap=ec)
        errs = structural + v["errors"]
        if not errs:
            deck["attempts"] = f"repair-{attempt}"
            deck["repaired_by_llm"] = True
            deck["repair_attempts"] = attempt
            joblog.bound_log(f"{part_id} 幻灯片规划由模型修复通过（第 {attempt} 次，{len(deck['slides'])} 页）", level="ok")
            return deck
        current, cur_errors = deck, errs                # 下一次基于最新一版继续改
    raise PlanError(f"{part_id} 幻灯片规划经 {max_attempts} 次模型修复仍不合格：" + "；".join(cur_errors[:4]))
