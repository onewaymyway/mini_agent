"""一句话起稿 S3：采用构思稿——把某一版转成一批**待确认提议**，不直接写卡片。

  * 复用现有提议区：作者逐项采纳 / 拒绝，采纳前重新校验；已有内容不会被静默覆盖。
  * 映射（draft 部分 → 提议）：
      premise         → premise set（卡片已有不同命题时标冲突；相同则跳过）
      storyline       → ideas add 一条（标签「故事走向」，卡片没有对应模块，存成灵感以免丢失）
      world           → 每个条目一条 world add（与卡片里同文字的条目跳过）
      cast            → 按名字对应已有角色：同名且有差异 → cast set（标冲突）；同名无差异 → 跳过；
                        不同名 → cast add（id 冲突时自动换新 id）；关系里的角色 id 同步改写
      plot            → 一条 plot set，只含与卡片不同的键；卡片对应键已有内容时标冲突，空卡片不标
      open_questions  → 每个问题一条 open_questions add（与已有同文字跳过）
  * 每条登记前先在卡片副本上试应用，无效的跳过并说明原因，绝不登记无效提议。
  * 同一版本上一次采用的提议还有待确认的，拒绝再次采用（避免重复堆积）。
"""
from __future__ import annotations

import copy
import uuid
from typing import Any, Dict, List, Optional

from novel_writer.brainstorm import cards as C
from novel_writer.brainstorm import distill as DS
from novel_writer.brainstorm import proposals as P
from novel_writer.brainstorm import seed_draft as SD
from novel_writer.brainstorm.workspace import BrainstormError, BrainstormWorkspace

STORYLINE_TAG = "故事走向"


def _batch_prefix(ver: int) -> str:
    return f"seed-v{ver:03d}-"


def _item_text(it: Any) -> str:
    return (it.get("text") if isinstance(it, dict) else it or "").strip() if it else ""


def _plan_cast(ws: BrainstormWorkspace, draft_cast: Dict[str, Any]) -> (List[Dict[str, Any]], List[str]):
    cur = C.get_module(ws, "cast")
    by_name = {(c.get("name") or "").strip(): cid for cid, c in cur.items()}
    taken = set(cur)
    idmap: Dict[str, str] = {}
    for did, card in draft_cast.items():
        name = card["name"].strip()
        if name in by_name:
            idmap[did] = by_name[name]
        else:
            new = did
            n = 2
            while new in taken:
                new, n = f"{did}_{n}", n + 1
            taken.add(new)
            idmap[did] = new

    def remap(rel: str) -> str:
        head, sep, tail = rel.partition(":")
        return f"{idmap.get(head.strip(), head.strip())}:{tail}" if sep else rel

    items, skipped = [], []
    for did, card in draft_cast.items():
        card = dict(card, relations=[remap(r) for r in card.get("relations", [])])
        eid = idmap[did]
        if eid in cur:
            diff = {k: v for k, v in card.items() if cur[eid].get(k, [] if k == "relations" else "") != v}
            if not diff:
                skipped.append(f"角色「{card['name']}」与卡片相同，跳过")
                continue
            items.append({"module": "cast", "op": "set", "target": eid, "value": diff,
                          "reason": f"构思稿中的角色「{card['name']}」与卡片里的同名角色有差异",
                          "conflict": f"修改已有角色 {eid}（{cur[eid].get('name', '')}）：" + "、".join(sorted(diff))})
        else:
            items.append({"module": "cast", "op": "add", "target": eid, "value": card,
                          "reason": f"构思稿新增角色「{card['name']}」", "conflict": ""})
    return items, skipped


def plan(ws: BrainstormWorkspace, ver: Optional[int] = None, sections: Optional[List[str]] = None) -> Dict[str, Any]:
    """只计算不写入：返回 {ver, items, skipped}。items 的每条含 section / module / op / target / value / reason / conflict。"""
    rec = SD.load(ws, ver)
    draft = SD.validate_draft(rec["draft"])
    chosen = SD.SECTIONS if not sections else tuple(s for s in SD.SECTIONS if s in set(sections))
    bad = [s for s in (sections or []) if s not in SD.SECTIONS]
    if bad:
        raise BrainstormError("未知部分：" + "、".join(bad))
    items: List[Dict[str, Any]] = []
    skipped: List[str] = []

    def add(section: str, module: str, op: str, target, value, reason: str, conflict: str = "") -> None:
        items.append({"section": section, "module": module, "op": op, "target": target, "value": value,
                      "reason": reason, "conflict": conflict})

    for sec in chosen:
        label = SD.SECTION_LABELS[sec]
        if sec == "premise":
            cur = C.get_module(ws, "premise").strip()
            if cur == draft["premise"]:
                skipped.append("核心命题与卡片相同，跳过")
            else:
                conflict = f"与现有核心命题不同：现有「{cur[:80]}」" if cur else ""
                add(sec, "premise", "set", None, draft["premise"], "构思稿的核心命题", conflict)
        elif sec == "storyline":
            text = SD.storyline_text(draft["storyline"])
            if DS._is_duplicate(ws, "ideas", "add", {"text": text}):
                skipped.append("故事走向已在灵感池里，跳过")
            else:
                add(sec, "ideas", "add", None, {"text": text, "tags": [STORYLINE_TAG]}, "构思稿的故事走向（卡片没有对应模块，存为灵感）")
        elif sec == "world":
            cur = C.get_module(ws, "world")
            for key in C.WORLD_LISTS:
                existing = {_item_text(x) for x in cur.get(key, [])}
                for it in draft["world"][key]:
                    if _item_text(it) in existing:
                        skipped.append(f"世界观 {key} 已有「{_item_text(it)[:20]}」，跳过")
                        continue
                    add(sec, "world", "add", key, it, f"构思稿的世界观（{key}）")
        elif sec == "cast":
            its, sk = _plan_cast(ws, draft["cast"])
            for i in its:
                add(sec, i["module"], i["op"], i["target"], i["value"], i["reason"], i["conflict"])
            skipped += sk
        elif sec == "plot":
            cur = C.get_module(ws, "plot")
            diff = {k: draft["plot"][k] for k in ("acts", "twists", "endings") if draft["plot"][k] != cur.get(k)}
            if not diff:
                skipped.append("核心剧情与卡片相同，跳过")
            else:
                keys = [k for k in diff if cur.get(k)]
                add(sec, "plot", "set", None, diff, "构思稿的核心剧情",
                    ("替换现有剧情字段：" + "、".join(keys)) if keys else "")
        elif sec == "open_questions":
            for q in draft["open_questions"]:
                if DS._is_duplicate(ws, "open_questions", "add", {"text": q["text"]}):
                    skipped.append(f"待定问题已存在，跳过：{q['text'][:20]}")
                    continue
                add(sec, "open_questions", "add", None, {"text": q["text"]}, "构思稿的待定问题")
    ok: List[Dict[str, Any]] = []
    for it in items:                                   # 登记前试应用：无效的不登记
        try:
            DS._dry_run(ws, it["module"], it["op"], it["target"], it["value"])
        except BrainstormError as exc:
            skipped.append(f"无效，跳过（{SD.SECTION_LABELS[it['section']]}）：{exc}")
            continue
        ok.append(it)
    return {"ver": rec["ver"], "items": ok, "skipped": skipped}


def adopt(ws: BrainstormWorkspace, ver: Optional[int] = None, sections: Optional[List[str]] = None) -> Dict[str, Any]:
    """把构思稿（某一版、可只选部分）登记为一批待确认提议。不写卡片。"""
    p = plan(ws, ver, sections)
    prefix = _batch_prefix(p["ver"])
    stale = [i for i in P.list_items(ws, "pending") if str(i.get("batch") or "").startswith(prefix)]
    if stale:
        raise BrainstormError(f"构思稿 v{p['ver']:03d} 上一次采用还有 {len(stale)} 条提议待确认，请先在提议区处理完（采纳或拒绝）")
    if not p["items"]:
        raise BrainstormError("没有可采用的内容（全部与卡片相同或无效）：" + "；".join(p["skipped"][:5]))
    batch = prefix + uuid.uuid4().hex[:4]
    created = [P.create(ws, it["module"], it["op"], it["value"], target=it["target"],
                        reason=f"[构思稿 v{p['ver']:03d}] {it['reason']}", batch=batch, conflict=it["conflict"])
               for it in p["items"]]
    return {"batch": batch, "ver": p["ver"], "proposals": created, "skipped": p["skipped"],
            "conflicts": sum(1 for c in created if c["conflict"])}
