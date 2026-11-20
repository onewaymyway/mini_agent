"""R5 以大纲为准的偏离检查：大纲是权威，正文是被检查对象。只产出「改正文以符合大纲」的修改意见。

权威大纲 = 检查版本（默认 head）的蓝图。若作者采纳过重生成大纲（R4），它已经是当前蓝图，无需单独指定。

两部分：
  * 确定性（不调用 LLM）：正文存在但蓝图中没有的章节（多写）；蓝图中有但正文没有的章节（少写）；
    字数相对预算偏离过大；出场人物与大纲的 characters_present 不一致（多出 / 缺少）。
    这些是规则比对，可能包含未被事实卡提取到的群演，因此统一标为「轻」，不经仲裁。
  * LLM（需要 invoker）：逐批（每批 OUTLINE_BATCH 章）对照大纲条目（功能、类型、视角、张力、出场人物，
    以及大纲先行书的章节简述必须事件）与事实卡，判断：多写（正文有大纲没有的重要事件）、
    少写（大纲要求的事件缺失）、走样（事件被改写）、顺序（信息披露或事件顺序与大纲不符）、
    幕边界（事件落在了大纲规定的幕之外）。每条偏离必须带正文引文（子串核对）；少写可无引文。

产出统一为问题池中的 source="outline"、category="outline_deviation"，只读版本，只写 _revision/review/。
处理方式由 review_adopt.options 给出：「以大纲为准修改正文」或「不处理」（驳回）。
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

from novel_writer.revision_flow import outline_regen as OR
from novel_writer.revision_flow import review_cards as RC
from novel_writer.revision_flow import review_checks as RK
from novel_writer.revision_flow import review_llm as RL
from novel_writer.revision_flow.common import FlowError, load_json, save_json, version_dir
from novel_writer.textio import read_text_lenient

SOURCE = "outline"
CATEGORY = "outline_deviation"
SKILL = "consistency-checker"
BATCH = 20
TYPES = ("多写", "少写", "走样", "顺序", "幕边界")
TYPE_CODE = {"多写": "extra", "少写": "missing", "走样": "drift", "顺序": "order", "幕边界": "act"}
NEED_EVIDENCE = ("多写", "走样", "顺序", "幕边界")          # 少写可以没有正文引文（事件就是没写出来）
WORD_HIGH, WORD_LOW = 1.5, 0.6                              # 字数相对预算的偏离阈值
STATE = "outline_check_state.json"
BRIEF_REL = "大纲规划/章节简述"


def _s(v: Any) -> str:
    return v.strip() if isinstance(v, str) else ""


def _chars(text: str) -> int:
    return sum(1 for c in text if not c.isspace())


def _fid(code: str, key: str) -> str:
    return RK._fid(f"outline_{code}", key)


def _finding(code: str, key: str, *, severity: str, chapters: List[int], title: str, description: str,
             evidence: List[Dict[str, Any]], source_kind: str, evidence_ok: bool) -> Dict[str, Any]:
    return {"id": _fid(code, key), "source": SOURCE, "check": f"outline_{code}", "category": CATEGORY,
            "severity": severity, "chapters": sorted(set(chapters)), "title": title, "description": description,
            "evidence": evidence, "evidence_ok": evidence_ok, "origin_kind": source_kind,
            "needs_adjudication": False, "options": [], "status": "open", "notes": ""}


# ─────────────────────────────── 确定性部分 ───────────────────────────────

def deterministic(bp: Dict[str, Any], cards: Dict[int, Dict[str, Any]], texts: Dict[int, str],
                  names: Dict[str, str]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    outline = {c["id"]: c for c in bp["chapters"]}
    body = set(texts)
    for n in sorted(body - set(outline)):
        out.append(_finding("extra_chapter", str(n), severity="中", chapters=[n],
                            title=f"第{n}章在大纲中没有对应条目",
                            description="正文存在这一章，但蓝图中没有它。可能是多写的章节，或蓝图漏登记。",
                            evidence=[{"chapter": n, "quote": "", "verified": False, "source": "card",
                                       "note": "正文存在，蓝图无条目"}], source_kind="deterministic", evidence_ok=True))
    for n in sorted(set(outline) - body):
        out.append(_finding("missing_chapter", str(n), severity="中", chapters=[n],
                            title=f"第{n}章只在大纲中，正文尚未写出",
                            description="蓝图中有这一章，但版本中没有对应正文。若是计划中的后续章节可忽略；否则属于少写。",
                            evidence=[], source_kind="deterministic", evidence_ok=True))
    for n in sorted(body & set(outline)):
        c = outline[n]
        words = _chars(texts[n])
        budget = c.get("word_budget")
        if isinstance(budget, int) and budget > 0:
            ratio = words / budget
            if ratio > WORD_HIGH or ratio < WORD_LOW:
                out.append(_finding("word", str(n), severity="轻", chapters=[n],
                                    title=f"第{n}章字数相对预算偏离",
                                    description=f"实际约 {words} 字，预算 {budget} 字（{ratio:.0%}）。偏离过大时节奏与大纲的分量可能不符。",
                                    evidence=[{"chapter": n, "quote": "", "verified": False, "source": "card",
                                               "note": f"字数 {words}"}], source_kind="deterministic", evidence_ok=True))
        planned = set(c.get("characters_present") or [])
        if planned and n in cards:
            actual = {RC.canon_name(x["name"], names)[0] for x in cards[n]["characters"]}
            extra, missing = sorted(actual - planned), sorted(planned - actual)
            if extra:
                out.append(_finding("character_extra", str(n), severity="轻", chapters=[n],
                                    title=f"第{n}章出现了大纲未列出的人物",
                                    description=f"正文出现：{'、'.join(extra)}；大纲出场为 {'、'.join(sorted(planned))}。"
                                                "可能只是群演，若重要则需要修正大纲或正文。",
                                    evidence=[{"chapter": n, "quote": "", "verified": False, "source": "card",
                                               "note": "、".join(extra)}], source_kind="deterministic", evidence_ok=True))
            if missing:
                out.append(_finding("character_missing", str(n), severity="轻", chapters=[n],
                                    title=f"第{n}章缺少大纲要求的人物",
                                    description=f"大纲要求出场：{'、'.join(missing)}，正文事实卡中没有提取到。",
                                    evidence=[{"chapter": n, "quote": "", "verified": False, "source": "card",
                                               "note": "、".join(missing)}], source_kind="deterministic", evidence_ok=True))
    return out


# ─────────────────────────────── LLM 部分 ───────────────────────────────

_FORMAT = ('{"deviations": [{"chapter": 章号, "type": "多写|少写|走样|顺序|幕边界", "severity": "严重|中|轻", '
           '"title": "简短标题", "outline_ref": "大纲对应的要求（引自大纲条目）", "description": "偏离说明", '
           '"evidence": [{"chapter": 章号, "quote": "正文中逐字摘抄的一句话"}]}]}')


def _brief_text(root: Path, n: int) -> str:
    p = root / BRIEF_REL / f"第{n:03d}章.json"
    data = load_json(p, default=None)
    if not isinstance(data, dict):
        return ""
    key = data.get("key") or {}
    ev = "；".join(key.get("must_events") or [])
    return f"  章节简述必须事件：{ev or '—'}"


def _batch_prompt(batch: List[int], bp_by: Dict[int, Dict[str, Any]], cards, paths, acts_text: str,
                  root: Path, out_file: Path, errors: List[str]) -> str:
    blocks = []
    for n in batch:
        o, c = bp_by[n], cards[n]
        brief = _brief_text(root, n)
        blocks.append(
            f"第{n}章《{o.get('title', '')}》　正文路径：{paths[n]}\n"
            f"  【大纲】功能：{o.get('function') or '—'}；类型：{o.get('chapter_type') or '—'}；"
            f"视角：{o.get('pov') or '—'}；张力：{o.get('tension_level')}；"
            f"出场：{'、'.join(o.get('characters_present') or []) or '—'}\n"
            + (brief + "\n" if brief else "")
            + f"  【正文事实卡】事件：{'；'.join(c['events'][:8]) or '—'}；"
              f"人物：{'、'.join(x['name'] for x in c['characters']) or '—'}；结尾：{c['ending_state'] or '—'}")
    parts = [
        "【任务】以大纲为准检查正文偏离：大纲是权威，正文是被检查的对象。只找偏离，不要评价文笔；没有偏离就写空列表。",
        f"【全书幕划分（大纲）】\n{acts_text}",
        "【本批章节】\n" + "\n\n".join(blocks),
        "【偏离类型】多写：正文有大纲没有的重要事件或人物；少写：大纲要求的事件缺失；"
        "走样：事件发生了但被改成了与大纲不同的样子；顺序：信息披露或事件先后与大纲不符；"
        "幕边界：事件落在了大纲规定的幕之外。",
        "【证据】多写、走样、顺序、幕边界必须给出正文引文（章号 + 逐字摘抄的一句话，可读取上面的正文路径）；"
        "少写可以没有引文（写 evidence: []）。outline_ref 写大纲中对应的要求。",
        f"【输出】把一个 JSON 对象写入文件：{out_file}\n格式：{_FORMAT}\n只写 JSON，不要代码围栏，不要多余文字。",
    ]
    if errors:
        parts.append("【上一次输出未通过校验】\n" + "\n".join(f"- {e}" for e in errors))
    return "\n\n".join(parts) + "\n"


def _parse(raw: str, batch: List[int]) -> List[Dict[str, Any]]:
    try:
        data = json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise FlowError(f"不是合法 JSON：{exc}") from None
    items = data.get("deviations") if isinstance(data, dict) else None
    if not isinstance(items, list):
        raise FlowError("deviations 必须是列表（没有偏离时写空列表）")
    out = []
    for it in items:
        if not isinstance(it, dict) or isinstance(it.get("chapter"), bool) or not isinstance(it.get("chapter"), int):
            raise FlowError("每一项需要整数 chapter")
        if it["chapter"] not in batch:
            raise FlowError(f"第 {it['chapter']} 章不在本批范围内")
        if it.get("type") not in TYPES:
            raise FlowError(f"type 必须是 {list(TYPES)} 之一")
        if it.get("severity") not in RK.SEVERITIES:
            raise FlowError(f"severity 必须是 {list(RK.SEVERITIES)} 之一")
        title, desc = _s(it.get("title")), _s(it.get("description"))
        if not title or not desc:
            raise FlowError("每条偏离都需要 title 与 description")
        ev = it.get("evidence")
        if not isinstance(ev, list):
            raise FlowError("evidence 必须是列表")
        evidence = []
        for e in ev:
            if not isinstance(e, dict) or isinstance(e.get("chapter"), bool) or not isinstance(e.get("chapter"), int):
                raise FlowError("evidence 的 chapter 必须是整数")
            evidence.append({"chapter": e["chapter"], "quote": _s(e.get("quote"))})
        if it["type"] in NEED_EVIDENCE and not any(e["quote"] for e in evidence):
            raise FlowError(f"{it['type']} 需要至少一条正文引文")
        out.append({"chapter": it["chapter"], "type": it["type"], "severity": it["severity"], "title": title,
                    "description": desc, "outline_ref": _s(it.get("outline_ref")), "evidence": evidence})
    return out


def _llm_finding(item: Dict[str, Any], texts: Dict[int, str], key: str) -> Dict[str, Any]:
    evidence = []
    for e in item["evidence"]:
        text = texts.get(e["chapter"], "")
        ok = bool(e["quote"]) and RC.quote_in_text(e["quote"], RC.normalize_for_match(text))
        evidence.append({"chapter": e["chapter"], "quote": e["quote"], "verified": ok, "source": "quote", "note": ""})
    if item["type"] == "少写" and not evidence:
        evidence_ok = True                                     # 缺失类没有正文可引
    else:
        evidence_ok = bool(evidence) and all(e["verified"] for e in evidence)
    desc = item["description"] + (f"\n大纲要求：{item['outline_ref']}" if item["outline_ref"] else "")
    return _finding(TYPE_CODE[item["type"]], f"{item['chapter']}|{item['title']}", severity=item["severity"],
                    chapters=[item["chapter"]], title=f"[{item['type']}] {item['title']}", description=desc,
                    evidence=evidence, source_kind="llm", evidence_ok=evidence_ok)


def _acts_text(bp: Dict[str, Any]) -> str:
    acts = (bp.get("structure") or {}).get("acts") or []
    return "\n".join(f"- {a.get('name')}：第 {a['chapter_range'][0]}–{a['chapter_range'][1]} 章" for a in acts) or "（无幕划分）"


def _llm_stage(ws, vid, bp, cards, texts, paths, invoker, say, failed, state, keys):
    """返回 (问题列表, 实际调用次数)。已缓存的批次不再调用。"""
    outline = {c["id"]: c for c in bp["chapters"]}
    batch_all = [n for n in sorted(set(texts) & set(outline)) if n in cards]
    root = version_dir(ws, vid)
    acts = _acts_text(bp)
    budget = RL._Budget(0)
    out: List[Dict[str, Any]] = []
    say(f"大纲偏离（LLM）：{len(batch_all)} 章，分 {max(1, -(-len(batch_all) // BATCH))} 批")
    for i in range(0, len(batch_all), BATCH):
        batch = batch_all[i:i + BATCH]
        sig = hashlib.sha1(json.dumps([[n, cards[n]["source_sha256"], outline[n]] for n in batch],
                                      ensure_ascii=False, sort_keys=True, default=str).encode("utf-8")
                           + acts.encode("utf-8")).hexdigest()[:10]
        key = f"b{batch[0]:03d}-{batch[-1]:03d}-{sig}"
        keys.append(key)
        if key not in state["batches"]:
            say(f"第 {batch[0]}–{batch[-1]} 章：对照大纲判定偏离")
            res, errs = RL._ask(ws, vid, kind="outline_check", key=key, skill=SKILL, invoker=invoker, budget=budget,
                                make_prompt=lambda o, e, b=batch: _batch_prompt(b, outline, cards, paths, acts,
                                                                                root, o, e),
                                parse=lambda raw, b=batch: _parse(raw, b), say=say)
            if res is None:
                failed.append({"batch": key, "errors": errs})
                say(f"  判定失败：{'；'.join(errs)}")
                continue
            state["batches"][key] = {"items": res}
            save_json(RK.review_dir(ws, vid) / STATE, state)
        for item in state["batches"][key]["items"]:
            out.append(_llm_finding(item, texts, key))
    return out, budget.calls


# ─────────────────────────────── 入口与合并 ───────────────────────────────

def _merge(ws, vid: str, findings: List[Dict[str, Any]]) -> Dict[str, Any]:
    pool = RK.load_findings(ws, vid)
    old = {f["id"]: f for f in pool["findings"] if f.get("source") == SOURCE}
    base = [f for f in pool["findings"] if f.get("source") != SOURCE]
    for f in findings:
        prev = old.get(f["id"])
        if prev:
            f["status"], f["notes"] = prev.get("status", "open"), prev.get("notes", "")
    merged = base + findings
    merged.sort(key=lambda f: (RK._SEV_ORDER.get(f["severity"], 9), f["category"], f["chapters"][:1], f["id"]))
    pool["findings"] = merged
    _save(ws, vid, pool)
    return pool


def _save(ws, vid: str, pool: Dict[str, Any]) -> None:
    save_json(RK.findings_path(ws, vid), pool)
    (RK.review_dir(ws, vid) / "report.md").write_text(RK.render_report(pool), encoding="utf-8")


def run_check(ws, version: Optional[str] = None, invoker=None, progress=None) -> Dict[str, Any]:
    """大纲偏离检查。invoker 为空时只做确定性部分。返回 {version, counts, failed, calls, findings}。"""
    say = progress or (lambda _t: None)
    vid = RC.resolve_version(ws, version)
    bp = OR.current_blueprint(ws, vid)
    cards, missing = RC.load_cards(ws, vid)
    if missing:
        raise FlowError(f"有 {len(missing)} 章没有事实卡（第{'、'.join(str(n) for n in missing)}章），请先生成事实卡")
    sources = RC.list_chapter_sources(ws, vid)
    texts: Dict[int, str] = {}
    paths: Dict[int, Path] = {}
    for src in sources:
        texts[src["number"]], _ = read_text_lenient(src["path"])
        paths[src["number"]] = src["path"]
    names = RC.known_names(ws, vid)
    say(f"大纲偏离检查：版本 {vid}（权威大纲 = 该版本蓝图，{len(bp['chapters'])} 条）")
    findings = deterministic(bp, cards, texts, names)
    say(f"确定性比对：{len(findings)} 条")
    failed: List[Dict[str, Any]] = []
    calls = 0
    if invoker is not None:
        state = load_json(RK.review_dir(ws, vid) / STATE, default=None) or {"batches": {}}
        keys: List[str] = []
        llm_findings, calls = _llm_stage(ws, vid, bp, cards, texts, paths, invoker, say, failed, state, keys)
        findings += llm_findings
        state["batches"] = {k: v for k, v in state["batches"].items() if k in set(keys)}   # 清理过期批次
        save_json(RK.review_dir(ws, vid) / STATE, state)
    pool = _merge(ws, vid, findings)
    counts: Dict[str, int] = {}
    for f in findings:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    say(f"偏离检查完成：{len(findings)} 条，失败批次 {len(failed)}")
    return {"version": vid, "counts": counts, "failed": failed, "calls": calls, "findings": findings,
            "llm": invoker is not None}
