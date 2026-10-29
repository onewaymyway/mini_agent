"""novel_writer/act_planning.py — 逐幕滚动规划：蓝图只细化到当前幕，后续幕在进入前再补章节条目。

背景：整本蓝图一次性由 LLM 输出，章节表常被截断（例如规划 50 章却只写出第一幕的 12 章），
流水线按总章数展开后，第 13 章因找不到蓝图条目而「没有字数配额」。

做法：
  plan_act:N          （LLM）进入第 N 幕前，只为本幕写章节条目 -> 大纲规划/蓝图/_act_N_draft.json。
                      输入含上一幕的全部幕末报告、已采纳的整改项、主角规范名（以规划为准）。
  blueprint_extend:N  （确定性 Python）校验后把本幕章节并入蓝图与字数配额，已有章节不动。

只有蓝图缺少某幕章节条目时，build_plan 才会插入这两步；已覆盖的幕不受影响。
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional, Set, Tuple

from novel_writer import steps as S
from novel_writer.fsutil import atomic_write_json, read_json
from novel_writer.workspace import Workspace

BUDGET_TOLERANCE = 0.25      # 本幕章节字数预算之和与幕预算的允许偏差
_PAREN = re.compile(r"[（(].*$")


def _int(v: Any) -> Optional[int]:
    return v if isinstance(v, int) and not isinstance(v, bool) else None


def acts_of(blueprint: Optional[Dict[str, Any]], total: int) -> List[Tuple[int, int, int]]:
    """[(幕号, 起章, 止章)]，按起章排序；止章不超过总章数。"""
    out: List[Tuple[int, int, int]] = []
    for a in ((blueprint or {}).get("structure") or {}).get("acts") or []:
        if not isinstance(a, dict):
            continue
        rng = a.get("chapter_range") or []
        act = _int(a.get("act"))
        if act is None or len(rng) != 2 or _int(rng[0]) is None or _int(rng[1]) is None:
            continue
        start, end = rng[0], min(rng[1], total) if total else rng[1]
        if 1 <= start <= end:
            out.append((act, start, end))
    return sorted(out, key=lambda t: t[1])


def covered_ids(blueprint: Optional[Dict[str, Any]]) -> Set[int]:
    return {ch["id"] for ch in (blueprint or {}).get("chapters") or []
            if isinstance(ch, dict) and _int(ch.get("id")) is not None}


def missing_acts(blueprint: Optional[Dict[str, Any]], total: int) -> List[Tuple[int, int, int]]:
    """蓝图章节条目不完整的幕（范围内任一章缺条目即算缺）。"""
    have = covered_ids(blueprint)
    return [(a, s, e) for a, s, e in acts_of(blueprint, total) if any(n not in have for n in range(s, e + 1))]


def act_budget(blueprint: Optional[Dict[str, Any]], act: int) -> Optional[int]:
    for a in ((blueprint or {}).get("structure") or {}).get("acts") or []:
        if isinstance(a, dict) and a.get("act") == act:
            return _int(a.get("word_budget"))
    return None


def canonical_protagonist(ws: Workspace, blueprint: Optional[Dict[str, Any]]) -> Optional[str]:
    """主角规范名——以规划（蓝图/人物档案）为准，幕末报告里出现的其它写法一律视为错误。
    顺序：人物档案中 role=protagonist 的角色名 → 第 1 章 pov → 蓝图中出场最多的角色。"""
    try:
        from novel_writer import character_store as CS
        chars = CS.load_or_none(ws.path("characters_dir"), ws.path("characters"), ws.path("characters_legacy")) or {}
        for rec in chars.values():
            if isinstance(rec, dict) and str(rec.get("role") or "").lower() == "protagonist" and rec.get("name"):
                return str(rec["name"]).strip()
    except Exception:  # noqa: BLE001 - 人物档案损坏不应阻断规划，退回蓝图
        pass
    chapters = [c for c in (blueprint or {}).get("chapters") or [] if isinstance(c, dict)]
    for ch in sorted(chapters, key=lambda c: _int(c.get("id")) or 10 ** 9):
        pov = _PAREN.sub("", str(ch.get("pov") or "")).strip()
        if pov:
            return pov
    counts: Dict[str, int] = {}
    for ch in chapters:
        for raw in ch.get("characters_present") or []:
            name = _PAREN.sub("", str(raw)).strip()
            if name:
                counts[name] = counts.get(name, 0) + 1
    return max(counts, key=counts.get) if counts else None


def previous_act_report_paths(act: int, acts: List[Tuple[int, int, int]]) -> List[str]:
    """上一幕（按幕序）的幕末报告相对路径，供 plan_act 读取。"""
    order = [a for a, _s, _e in acts]
    if act not in order or order.index(act) == 0:
        return []
    prev = order[order.index(act) - 1]
    return [S.rel("act_health_report", act=prev), S.rel("act_pacing_report", act=prev),
            S.rel("act_theme_report", act=prev), S.rel("act_style_report", act=prev),
            S.rel("world_usage_report", n=prev)]


# ── 产出校验 ───────────────────────────────────────────────────────────────


def draft_problems(ws: Workspace, step: Any) -> List[str]:
    """plan_act 的产出：本幕每一章都有条目，章号落在本幕范围内，字数预算合理，伏笔兑现章不越界。"""
    from novel_writer import validators as V
    extra = getattr(step, "extra", None) or {}
    act, start, end = extra.get("act"), extra.get("start"), extra.get("end")
    path = ws.path("act_blueprint_draft", act=act)
    if not path.is_file():
        return [f"缺少本幕章节草稿：{path.name}"]
    data, err, fenced = V._read_json_loose(path)
    if err is not None:
        return [f"本幕章节草稿不是合法 JSON（{err}）。请只输出一个 JSON 对象：{{\"chapters\": [...]}}"]
    chapters = data.get("chapters") if isinstance(data, dict) else None
    if not isinstance(chapters, list) or not chapters:
        return ["草稿缺少非空的 chapters 数组（只需要本幕的章节条目）"]
    problems: List[str] = []
    seen: Set[int] = set()
    changed = fenced
    total = 0
    from novel_writer import pipeline as P
    bp = _blueprint(ws)
    total_chapters = P.total_chapters(ws, bp)
    for i, ch in enumerate(chapters, start=1):
        if not isinstance(ch, dict):
            problems.append(f"第 {i} 条章节条目必须是对象")
            continue
        no = V.as_chapter_no(ch.get("id"))
        if no is None:
            problems.append(f"第 {i} 条章节的 id 必须是章号整数，当前为 {ch.get('id')!r}")
            continue
        if ch.get("id") != no:
            ch["id"] = no
            changed = True
        if not (start <= no <= end):
            problems.append(f"章号 {no} 不在第 {act} 幕范围 {start}–{end} 内（本草稿只写本幕）")
        if no in seen:
            problems.append(f"章号 {no} 重复")
        seen.add(no)
        wb = V.as_int(ch.get("word_budget"))
        if wb is None or wb <= 0:
            problems.append(f"第 {no} 章必须有正整数 word_budget，当前为 {ch.get('word_budget')!r}")
        else:
            if ch.get("word_budget") != wb:
                ch["word_budget"] = wb
                changed = True
            total += wb
        for fs in ch.get("foreshadowing") or []:
            rc = V.as_chapter_no(fs.get("planned_reveal_chapter")) if isinstance(fs, dict) else None
            if rc is not None and total_chapters and rc > total_chapters:
                problems.append(f"第 {no} 章的伏笔 {fs.get('id')} 计划兑现章 {rc} 超过全书总章数 {total_chapters}")
    absent = [n for n in range(start, end + 1) if n not in seen]
    if absent:
        problems.append(f"第 {act} 幕缺少这些章的条目：{absent[:20]}{'…' if len(absent) > 20 else ''}（必须覆盖 {start}–{end} 每一章）")
    budget = act_budget(bp, act)
    if budget and total and abs(total - budget) / budget > BUDGET_TOLERANCE:
        problems.append(f"本幕各章 word_budget 之和 {total} 与幕预算 {budget} 相差超过 {int(BUDGET_TOLERANCE * 100)}%，请调整")
    if problems:
        return problems
    if changed:
        atomic_write_json(path, data)
    return []


def _blueprint(ws: Workspace) -> Optional[Dict[str, Any]]:
    from novel_writer import blueprint_store as BS
    return BS.load_for_ws(ws, strict=False)


# ── 确定性并入 ─────────────────────────────────────────────────────────────


def h_blueprint_extend(ws: Workspace, step: S.StepDef, settings: Dict[str, Any], invoker=None) -> Dict[str, Any]:
    """把 plan_act 的本幕章节并入蓝图与字数配额。已有章节原样保留；内容无变化不写盘。"""
    from novel_writer import blueprint_store as BS
    from novel_writer.chapter_ops import ChapterOpError
    extra = step.extra or {}
    act = extra.get("act")
    probs = draft_problems(ws, step)
    if probs:
        raise ChapterOpError(f"第 {act} 幕章节草稿不合格：" + "；".join(probs[:5]))
    draft = read_json(ws.path("act_blueprint_draft", act=act), default={}) or {}
    bp = _blueprint(ws)
    if not isinstance(bp, dict):
        raise ChapterOpError("蓝图缺失或损坏，无法追加章节")
    existing = {ch["id"]: ch for ch in bp.get("chapters") or [] if isinstance(ch, dict) and _int(ch.get("id")) is not None}
    added = []
    for ch in draft["chapters"]:
        if ch["id"] in existing:
            continue                      # 已写好的/已规划的章节不被覆盖
        ch.setdefault("act", act)
        existing[ch["id"]] = ch
        added.append(ch["id"])
    bp["chapters"] = [existing[k] for k in sorted(existing)]
    try:
        BS.save_for_ws(ws, bp)
    except BS.BlueprintStoreError as exc:
        raise ChapterOpError("追加蓝图章节失败：" + "；".join(exc.problems))
    wc = read_json(ws.path("word_count"), default=None) or {}
    per = dict(wc.get("per_chapter_budget") or {})
    for n in added:
        per[str(n)] = existing[n].get("word_budget")
    if added:
        wc.update({"schema_version": wc.get("schema_version", 1), "per_chapter_budget": per,
                   "deviation_tolerance": wc.get("deviation_tolerance", 0.05)})
        wc.setdefault("total_target", (bp.get("meta") or {}).get("target_length"))
        atomic_write_json(ws.path("word_count"), wc)
    return {"summary": f"第 {act} 幕已追加 {len(added)} 章蓝图条目" + ("" if added else "（均已存在，无变化）")}
