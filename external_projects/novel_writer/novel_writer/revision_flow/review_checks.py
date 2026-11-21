"""R1 确定性检查与问题池（零 LLM）。

只读取 R0 的章节事实卡，按规则找出「候选问题」，写入问题池 findings.json 并渲染 report.md。
  * 这里的结果都是**候选**：规则看不到上下文（人物可能真的复活、场景可能真的跳转），
    带 needs_adjudication=True 的条目要在 R2 由 LLM 对照原文仲裁，或由作者自己判断；
  * 检查按维度开关（review_dimensions）；「未纳入评估的章节」不受开关影响，必须如实报告；
  * 问题 id 由「检查名 + 关键信息」哈希得到，重复运行 id 稳定；重新计算时保留作者已做的处理
    （采纳/驳回等状态）以及非确定性来源（LLM 等）的问题，只替换确定性来源的问题；
  * 只写 `_revision/review/rev_<版本>/`，不改版本、不写流水线状态。

已知未覆盖（后续阶段）：伏笔账本与事实卡动作的交叉核对；只对形如 F+数字 的伏笔编号做顺序检查，
自由文本的伏笔描述无法可靠匹配，不做。
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import re
from typing import Any, Dict, List, Optional

from novel_writer.revision_flow import config as CFG
from novel_writer.revision_flow import review_cards as RC
from novel_writer.revision_flow.common import FlowError, load_json, save_json, sub_dir

SEVERITIES = ("严重", "中", "轻")
_SEV_ORDER = {s: i for i, s in enumerate(SEVERITIES)}
STATUSES = ("open", "adopted", "rejected", "applied", "resolved", "still_open", "stale")
CATEGORY_LABEL = {
    "coverage": "评估覆盖", "contradiction": "前后矛盾", "continuity": "衔接连贯", "timeline": "时间线",
    "character_logic": "人物", "foreshadow": "伏笔", "pacing": "节奏", "redundancy": "冗余", "style_drift": "文风漂移",
    "outline_deviation": "大纲偏离",
    "character_profile": "人设不符", "logic": "逻辑硬伤",
}

ADJ_LABEL = {"confirmed": "确认为问题", "false_positive": "误报", "uncertain": "无法判定"}

_DEAD = re.compile(r"(死亡|身亡|去世|战死|被杀|遇害|殒命|病逝|牺牲|已死|死去)")
_NEG = "未没不险差假免"
_REVIVE = re.compile(r"(复活|重生|复生|还魂)")
_FS_ID = re.compile(r"^F\d+$", re.I)
_DAY = re.compile(r"第([0-9一二三四五六七八九十百]+)[天日]")
_CN = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}


def _now() -> str:
    return _dt.datetime.now().isoformat(timespec="seconds")


def review_id(version: str) -> str:
    return f"rev_{version}"


def review_dir(ws, version: str):
    d = sub_dir(ws, "review") / review_id(version)
    d.mkdir(parents=True, exist_ok=True)
    return d


def _fid(check: str, key: str) -> str:
    return "f_" + hashlib.sha1(f"{check}|{key}".encode("utf-8")).hexdigest()[:10]


def _finding(check: str, key: str, *, category: str, severity: str, chapters: List[int], title: str,
             description: str, evidence: List[Dict[str, Any]], adjudicate: bool) -> Dict[str, Any]:
    return {"id": _fid(check, key), "source": "deterministic", "check": check, "category": category,
            "severity": severity, "chapters": sorted(set(chapters)), "title": title, "description": description,
            "evidence": evidence, "needs_adjudication": adjudicate, "options": [], "status": "open",
            "notes": ""}


def _card_ev(n: int, note: str, term: str = "") -> Dict[str, Any]:
    """来自事实卡的陈述（没有原文引文）：R2 仲裁时用 term（章节里的定位词）在原文中取上下文核实。"""
    return {"chapter": n, "quote": "", "verified": False, "source": "card", "note": note, "term": term}


# ─────────────────────────────── 各项检查 ───────────────────────────────

def check_fact_conflicts(cards: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """同一主体的同一属性，在不同章节取值不同（候选矛盾）。"""
    groups: Dict[tuple, List[Dict[str, Any]]] = {}
    for n in sorted(cards):
        for f in cards[n]["facts_stated"]:
            groups.setdefault((f["subject"], f["attribute"]), []).append({**f, "chapter": n})
    out: List[Dict[str, Any]] = []
    for (subj, attr), items in groups.items():
        # 以首次出现为基准，找与之取值不同（互不包含）且在不同章节的条目
        first = items[0]
        base = RC.normalize_for_match(first["value"])
        diff = [i for i in items[1:] if i["chapter"] != first["chapter"]
                and RC.normalize_for_match(i["value"]) not in base and base not in RC.normalize_for_match(i["value"])]
        if not diff:
            continue
        ev_items = [first] + diff
        values = []
        for i in ev_items:
            if i["value"] not in values:
                values.append(i["value"])
        out.append(_finding(
            "fact_conflict", f"{subj}|{attr}", category="contradiction", severity="中",
            chapters=[i["chapter"] for i in ev_items],
            title=f"「{subj}」的「{attr}」前后取值不同",
            description=f"第{first['chapter']}章写为「{first['value']}」，之后又出现不同的写法：{'、'.join(values[1:])}。"
                        "可能是矛盾，也可能是情节中的真实变化，需要对照原文判断。",
            evidence=[{"chapter": i["chapter"], "quote": i["quote"], "verified": True, "source": "quote",
                       "note": f"{subj}·{attr} = {i['value']}"} for i in ev_items],
            adjudicate=True))
    return out


def check_departed_reappear(cards: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """人物在某章末状态为死亡，之后章节又作为在场人物出现（候选）。"""
    died: Dict[str, int] = {}
    died_state: Dict[str, str] = {}
    out: List[Dict[str, Any]] = []
    reported: set = set()
    for n in sorted(cards):
        for c in cards[n]["characters"]:
            name, state = c["name"], c["state_end"]
            if name in died and name not in reported and died[name] < n:
                if _REVIVE.search(state):
                    died.pop(name, None)
                    continue
                later = [m for m in sorted(cards) if m > died[name]
                         and any(x["name"] == name for x in cards[m]["characters"])]
                reported.add(name)
                out.append(_finding(
                    "departed_reappear", name, category="contradiction", severity="严重",
                    chapters=[died[name]] + later,
                    title=f"「{name}」第{died[name]}章已死亡，之后仍出现",
                    description=f"事实卡显示「{name}」在第{died[name]}章末已死亡，但第{'、'.join(str(m) for m in later)}章"
                                "又作为在场人物出现。可能是回忆/幻觉/被提及，也可能是遗漏的矛盾。",
                    evidence=[_card_ev(died[name], f"{name} 末状态：{died_state[name]}", name)]
                             + [_card_ev(m, f"{name} 在场", name) for m in later[:5]],
                    adjudicate=True))
            m = _DEAD.search(state)
            if m and name not in died and not any(ch in state[max(0, m.start() - 4):m.start()] for ch in _NEG):
                died[name], died_state[name] = n, state
    return out


def _cn_num(s: str) -> Optional[int]:
    if s.isdigit():
        return int(s)
    total, cur = 0, 0
    for ch in s:
        if ch in _CN:
            cur = _CN[ch]
        elif ch == "十":
            total += (cur or 1) * 10
            cur = 0
        elif ch == "百":
            total += (cur or 1) * 100
            cur = 0
        else:
            return None
    return total + cur or None


def check_timeline(cards: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """只比较形如「第N天/日」的明确时间标记；相邻的有标记章节里天数倒退则报候选。"""
    out: List[Dict[str, Any]] = []
    prev: Optional[tuple] = None
    for n in sorted(cards):
        m = _DAY.search(cards[n]["time_marker"])
        day = _cn_num(m.group(1)) if m else None
        if day is None:
            continue
        if prev and day < prev[1]:
            out.append(_finding(
                "timeline_regress", f"{prev[0]}|{n}", category="timeline", severity="中", chapters=[prev[0], n],
                title=f"第{prev[0]}章到第{n}章时间标记倒退",
                description=f"第{prev[0]}章标记为「{cards[prev[0]]['time_marker']}」，第{n}章标记为「{cards[n]['time_marker']}」，"
                            "天数反而变小。可能是倒叙/回忆，也可能是时间线错误。",
                evidence=[_card_ev(prev[0], f"时间标记：{cards[prev[0]]['time_marker']}", cards[prev[0]]["time_marker"]),
                          _card_ev(n, f"时间标记：{cards[n]['time_marker']}", cards[n]["time_marker"])],
                adjudicate=True))
        prev = (n, day)
    return out


def check_foreshadow_order(cards: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """只检查形如 F+数字 的伏笔编号：先回收后种下、回收却从未种下、种下后全书未回收。"""
    per: Dict[str, Dict[str, List[Dict[str, Any]]]] = {}
    for n in sorted(cards):
        for f in cards[n]["foreshadow"]:
            if _FS_ID.match(f["ref"]):
                per.setdefault(f["ref"].upper(), {}).setdefault(f["action"], []).append({**f, "chapter": n})
    out: List[Dict[str, Any]] = []
    for ref, acts in sorted(per.items()):
        plants, resolves = acts.get("plant", []), acts.get("resolve", [])
        if resolves and plants and resolves[0]["chapter"] < plants[0]["chapter"]:
            out.append(_finding(
                "foreshadow_order", f"{ref}|order", category="foreshadow", severity="中",
                chapters=[resolves[0]["chapter"], plants[0]["chapter"]],
                title=f"伏笔 {ref} 先回收后种下",
                description=f"第{resolves[0]['chapter']}章回收了 {ref}，但第一次种下在第{plants[0]['chapter']}章。",
                evidence=[{"chapter": x["chapter"], "quote": x["quote"], "verified": True, "source": "quote", "note": lab}
                          for x, lab in ((resolves[0], "回收"), (plants[0], "种下"))],
                adjudicate=True))
        elif resolves and not plants:
            r = resolves[0]
            out.append(_finding(
                "foreshadow_order", f"{ref}|noplant", category="foreshadow", severity="轻", chapters=[r["chapter"]],
                title=f"伏笔 {ref} 被回收但没有种下记录",
                description=f"第{r['chapter']}章回收了 {ref}，事实卡里找不到它的种下。可能是种下处没被提取到。",
                evidence=[{"chapter": r["chapter"], "quote": r["quote"], "verified": True, "source": "quote", "note": "回收"}],
                adjudicate=True))
        elif plants and not resolves:
            p = plants[0]
            out.append(_finding(
                "foreshadow_order", f"{ref}|unresolved", category="foreshadow", severity="轻", chapters=[p["chapter"]],
                title=f"伏笔 {ref} 种下后全书未见回收",
                description=f"第{p['chapter']}章种下了 {ref}，全书事实卡中没有回收它的记录。",
                evidence=[{"chapter": p["chapter"], "quote": p["quote"], "verified": True, "source": "quote", "note": "种下"}],
                adjudicate=True))
    return out


def check_unregistered(cards: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """多章出现却不在角色档案中的人物（只出现在一章的路人不报，避免噪声）。"""
    seen: Dict[str, List[int]] = {}
    for n in sorted(cards):
        for name in cards[n]["unregistered_characters"]:
            seen.setdefault(name, []).append(n)
    return [_finding("unregistered_character", name, category="character_logic", severity="轻", chapters=chs,
                     title=f"「{name}」多章出现但未登记",
                     description=f"「{name}」出现在第{'、'.join(str(c) for c in chs)}章，角色档案里没有它。"
                                 "若是重要人物，建议补登记；若只是群演可以忽略。",
                     evidence=[_card_ev(c, f"{name} 在场", name) for c in chs[:5]], adjudicate=False)
            for name, chs in sorted(seen.items()) if len(chs) >= 2]


def check_handoff(cards: Dict[int, Dict[str, Any]]) -> List[Dict[str, Any]]:
    """相邻章：上一章结尾地点与下一章开头地点明显不同（候选；场景切换很常见，所以只报「轻」）。"""
    out: List[Dict[str, Any]] = []
    for n in sorted(cards):
        nxt = cards.get(n + 1)
        if nxt is None:
            continue
        a, b = cards[n]["location_end"], nxt["location_start"]
        na, nb = RC.normalize_for_match(a), RC.normalize_for_match(b)
        if na and nb and na not in nb and nb not in na:
            out.append(_finding(
                "handoff_break", f"{n}|{n + 1}", category="continuity", severity="轻", chapters=[n, n + 1],
                title=f"第{n}章结尾与第{n + 1}章开头的地点不同",
                description=f"第{n}章结尾在「{a}」，第{n + 1}章开头在「{b}」。若中间有转场交代则无问题。",
                evidence=[_card_ev(n, f"结尾：{cards[n]['ending_state'] or a}", a),
                          _card_ev(n + 1, f"开头：{nxt['opening_state'] or b}", b)],
                adjudicate=True))
    return out


# 检查名 → (所属维度, 函数)
CHECKS = {
    "fact_conflict": ("contradiction", check_fact_conflicts),
    "departed_reappear": ("rule_legacy", check_departed_reappear),
    "timeline_regress": ("timeline", check_timeline),
    "foreshadow_order": ("foreshadow", check_foreshadow_order),
    "unregistered_character": ("character_logic", check_unregistered),
    "handoff_break": ("rule_legacy", check_handoff),
}


def check_coverage(missing: List[int]) -> List[Dict[str, Any]]:
    if not missing:
        return []
    return [_finding("coverage_missing", ",".join(str(n) for n in missing), category="coverage", severity="中",
                     chapters=missing, title=f"{len(missing)} 章未纳入评估",
                     description=f"第{'、'.join(str(n) for n in missing)}章没有可用的事实卡（尚未生成或生成失败），"
                                 "与它们有关的问题无法发现。请重新生成事实卡后再评估。",
                     evidence=[], adjudicate=False)]


# ─────────────────────────────── 问题池 ───────────────────────────────

def findings_path(ws, version: str):
    return review_dir(ws, version) / "findings.json"


def load_findings(ws, version: str) -> Dict[str, Any]:
    data = load_json(findings_path(ws, version), default=None)
    if isinstance(data, dict) and isinstance(data.get("findings"), list):
        return data
    return {"review_id": review_id(version), "version": version, "findings": []}


def run_checks(ws, version: Optional[str] = None, progress=None) -> Dict[str, Any]:
    """对版本做确定性检查，更新问题池与报告。至少需要一张事实卡。返回 {review_id, version, counts, findings, missing}。"""
    say = progress or (lambda _t: None)
    vid = RC.resolve_version(ws, version)
    cards, missing = RC.load_cards(ws, vid)
    if not cards:
        raise FlowError("没有可用的事实卡，请先生成事实卡（review-cards）")
    dims = set(CFG.load(ws)["review_dimensions"])
    fresh: List[Dict[str, Any]] = []
    for name, (dim, fn) in CHECKS.items():
        if dim in dims:
            found = fn(cards)
            fresh.extend(found)
            say(f"检查 {name}：{len(found)} 条候选")
        else:
            say(f"检查 {name}：维度 {dim} 未启用，跳过")
    fresh.extend(check_coverage(missing))

    old = load_findings(ws, vid)
    old_by_id = {f["id"]: f for f in old["findings"]}
    merged: List[Dict[str, Any]] = []
    for f in fresh:                       # 同 id 的问题保留作者已做的处理
        prev = old_by_id.get(f["id"])
        if prev:
            f["status"], f["notes"] = prev.get("status", "open"), prev.get("notes", "")
            if prev.get("options"):
                f["options"] = prev["options"]
            if prev.get("adjudication"):                    # R2 的仲裁结果随问题保留
                f["adjudication"] = prev["adjudication"]
        merged.append(f)
    merged.extend(f for f in old["findings"] if f.get("source") != "deterministic")   # 其它来源的问题原样保留
    merged.sort(key=lambda f: (_SEV_ORDER.get(f["severity"], 9), f["category"], f["chapters"][:1], f["id"]))
    data = {"review_id": review_id(vid), "version": vid, "updated_at": _now(), "findings": merged,
            "stats": {"chapters_with_cards": len(cards), "chapters_missing_cards": missing,
                      "dropped_items": sum(len(c["dropped"]) for c in cards.values())}}
    save_json(findings_path(ws, vid), data)
    (review_dir(ws, vid) / "report.md").write_text(render_report(data), encoding="utf-8")
    say(f"问题池已更新：共 {len(merged)} 条，缺卡章节 {len(missing)} 个")
    counts: Dict[str, int] = {}
    for f in merged:
        counts[f["severity"]] = counts.get(f["severity"], 0) + 1
    return {"review_id": data["review_id"], "version": vid, "counts": counts, "findings": merged, "missing": missing}


def render_report(data: Dict[str, Any]) -> str:
    fs = data["findings"]
    st = data.get("stats", {})
    lines = [f"# 全书评估报告（版本 {data['version']}）", "",
             f"- 纳入评估章节：{st.get('chapters_with_cards', 0)}　未纳入：{len(st.get('chapters_missing_cards', []))}"
             f"　因引文无法核对而丢弃的事实条目：{st.get('dropped_items', 0)}",
             f"- 问题共 {len(fs)} 条（严重 {sum(f['severity'] == '严重' for f in fs)} / 中 {sum(f['severity'] == '中' for f in fs)}"
             f" / 轻 {sum(f['severity'] == '轻' for f in fs)}）",
             "- 标「待仲裁」的是规则发现的**候选**，需对照原文确认后才算问题；所有问题都不会被自动修改。", ""]
    if not fs:
        lines.append("未发现问题。")
    for sev in SEVERITIES:
        group = [f for f in fs if f["severity"] == sev]
        if not group:
            continue
        lines += [f"## {sev}（{len(group)}）", ""]
        for f in group:
            tag = "　`待仲裁`" if f.get("needs_adjudication") else ""
            lines.append(f"### [{CATEGORY_LABEL.get(f['category'], f['category'])}] {f['title']}{tag}")
            lines.append(f"- 编号：{f['id']}　状态：{f['status']}　涉及章节：{'、'.join(str(c) for c in f['chapters'])}")
            lines.append(f"- {f['description']}")
            if f.get("adjudication"):
                adj = f["adjudication"]
                lines.append(f"- 模型仲裁：{ADJ_LABEL.get(adj.get('verdict'), adj.get('verdict'))}——{adj.get('reason', '')}")
            for e in f["evidence"]:
                body = f"“{e['quote']}”" if e.get("quote") else (e.get("note") or "")
                note = f"（{e['note']}）" if e.get("quote") and e.get("note") else ""
                lines.append(f"  - 第{e['chapter']}章：{body}{note}")
            lines.append("")
    return "\n".join(lines) + "\n"


def set_status(ws, version: str, fid: str, status: str, notes: Optional[str] = None) -> Dict[str, Any]:
    if status not in STATUSES:
        raise FlowError(f"状态必须是 {STATUSES} 之一")
    data = load_findings(ws, version)
    for f in data["findings"]:
        if f["id"] == fid:
            f["status"] = status
            if notes is not None:
                f["notes"] = notes
            save_json(findings_path(ws, version), data)
            (review_dir(ws, version) / "report.md").write_text(render_report(data), encoding="utf-8")
            return f
    raise FlowError(f"问题不存在：{fid}")
