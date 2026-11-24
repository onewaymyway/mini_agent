"""planning/topics.py — 选题运行（方案 §6.2–6.3、§9）。

一次运行：缺口检测 → 模板生成候选（标题/类型/层级/必须覆盖概念/互链）→ 去重与范围过滤
→ 中等相似度交模型判重（无模型则标记“未判重”）→ 打分排序 → 按 max_topics_per_cycle 截断
→ 入队（proposed；挡位与自动放行开关允许时直接 approved）。
开放命题（LLM 自由提题）默认关闭，需在设置中打开 automation.open_topics；提出的题目同样经过去重与范围过滤。
"""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from .. import joblog, settings, store
from ..digest import runner as drunner
from ..digest import summarize as sm
from ..library import catalog
from ..wiki import model, tree
from ..wiki import validate_util as vu
from . import dedup, gaps, states

KINDS = ("概念讲解", "对比", "教程", "综述", "FAQ", "案例")
LEVELS = ("入门", "进阶", "专题")
AUTO_GEARS = ("G2", "G3")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def template(gap: Dict[str, Any], names: Dict[str, str], data) -> Dict[str, Any]:
    t, n = gap["type"], gap["name"]
    node = gap.get("node")
    base = {"gap_key": gap["key"], "gap_type": t, "gap_reason": gap["reason"], "node": node,
            "links": list(gap.get("articles") or [])[:8], "severity": gap["severity"]}
    must = list(gap.get("concepts") or []) or [n]
    if t in ("G1", "G1s"):
        return {**base, "title": f"{n}：入门概览", "kind": "概念讲解", "level": "入门", "must_cover": must,
                "angle": "为该主题提供从零开始的概览，串起已有的概念"}
    if t == "G2":
        return {**base, "title": f"{n}是什么", "kind": "概念讲解", "level": "入门", "must_cover": must,
                "angle": f"专门讲解「{n}」，回答它是什么、为什么重要"}
    if t == "G3":
        return {**base, "title": f"{n}入门", "kind": "概念讲解", "level": "入门", "must_cover": must,
                "angle": f"为后续文章补上前置知识「{n}」"}
    if t == "G4":
        return {**base, "title": f"{n}的区别与联系", "kind": "对比", "level": "进阶", "must_cover": must,
                "angle": "用维度对比两个常被一起提到的概念"}
    if t == "G5":
        return {**base, "title": f"{n}入门指南", "kind": "教程", "level": "入门", "must_cover": must,
                "angle": "补上该主题缺失的入门内容，循序渐进"}
    if t == "G6":
        return {**base, "title": f"{n}综述", "kind": "综述", "level": "专题", "must_cover": must,
                "angle": "汇总该主题已有的文章，给出全局视图并链接到各篇"}
    return {**base, "title": n, "kind": "概念讲解", "level": "入门", "must_cover": must, "angle": ""}


# ------------------------------------------------------------------ 选题标题：参考文库风格，由模型撰写
GENERIC_TITLE = re.compile(r"(是什么|入门概览|入门指南|入门|综述)$")
TITLE_BATCH = 8


def _node_name(data, nid: str) -> str:
    for k, n in tree.nodes_at(data, tree.TOPIC):
        if k == nid:
            return n.get("name", nid)
    return nid


def style_profile(ctx) -> str:
    """文库的写作风格：文体与层级分布，以及代表性标题（带文体、层级），供撰写选题时模仿。"""
    rows = catalog.list_articles(ctx)
    kinds, levels, samples = Counter(), Counter(), []
    for r in rows:
        rec = drunner.load_digest(ctx, r["id"]) or {}
        if rec.get("status") != "ok":
            continue
        kinds[rec.get("kind") or "未知"] += 1
        levels[rec.get("level") or "未知"] += 1
        samples.append(f"- 「{rec.get('title') or r['title']}」（{rec.get('kind')}／{rec.get('level')}）")
    if not samples:
        return "文库暂无可参考的文章，请以章程的读者与范围为准。"
    dist = "、".join(f"{k} {v}" for k, v in kinds.most_common())
    lv = "、".join(f"{k} {v}" for k, v in levels.most_common())
    return (f"文库共 {len(samples)} 篇。文体分布：{dist}。层级分布：{lv}。\n"
            "代表性标题（学习其具体、有问题意识、点明角度的写法）：\n" + "\n".join(samples[:12]))


def _title_prompt(profile: str, items: List[Dict[str, Any]], scope_in: List[str]) -> str:
    lines = [f"tpid: {c['tpid']} ｜ 所属主题：{c['node_name']} ｜ 缺口：{c['gap_reason']} ｜ "
             f"必须覆盖的概念：{'、'.join(c['must_cover'])}" for c in items]
    return ("【阶段:选题标题】为下面每个选题写出有深度的标题，风格参照文库。只输出 YAML：\n"
            "items:\n  - {tpid: g1, title: 标题, angle: 一句话写清要解决的问题或读者收获, "
            "kind: 概念讲解|对比|教程|综述|FAQ|案例, level: 入门|进阶|专题}\n\n"
            "要求：\n"
            "1. 标题要具体，体现问题意识、比较、取舍、场景、误区或实践中的难点；\n"
            "2. 不得只写「X是什么」「X入门」「X入门指南」「X综述」或「X入门概览」这类模板，除非它本身是进阶的问题；\n"
            "3. 必须覆盖列出的概念，概念可以出现在标题中，但标题不能只是概念名；\n"
            "4. 标题与文库已有标题不得雷同；每个标题 8 到 40 字；\n"
            "5. kind 与 level 要与标题的写法相符（对比类写「…与…的区别」一类题目，进阶写机制与权衡）。\n\n"
            f"文库风格参考：\n{profile}\n\n章程范围：{'、'.join(scope_in) or '未限定'}\n\n选题：\n" + "\n".join(lines))


def _title_validator(expected: set, existing_titles: List[str]):
    def _v(obj: Dict[str, Any]) -> List[str]:
        items = obj.get("items")
        if not isinstance(items, list):
            return ["items 应为列表"]
        issues: List[str] = []
        seen = set()
        for it in items:
            if not isinstance(it, dict) or str(it.get("tpid")) not in expected:
                issues.append("每项需含 tpid，且 tpid 必须来自给定的选题")
                continue
            t = str(it.get("title") or "").strip()
            if not (8 <= len(t) <= 40):
                issues.append(f"{it.get('tpid')} 的标题应为 8 到 40 字")
            elif GENERIC_TITLE.search(t):
                issues.append(f"{it.get('tpid')} 的标题「{t}」是模板式写法，请写出具体的问题或角度")
            elif t in seen or t in existing_titles:
                issues.append(f"{it.get('tpid')} 的标题与其它标题雷同")
            if it.get("kind") not in KINDS or it.get("level") not in LEVELS:
                issues.append(f"{it.get('tpid')} 的 kind 或 level 不合法")
            if not str(it.get("angle") or "").strip():
                issues.append(f"{it.get('tpid')} 需要 angle")
            seen.add(t)
        return issues
    return _v


def _write_titles(ctx, llm, cands: List[Dict[str, Any]], data, scope_in, existing_titles, max_rounds) -> None:
    """用模型为缺口选题撰写标题与角度。失败的条目保留模板标题，不影响其它条目。"""
    if llm is None or not cands:
        return
    profile = style_profile(ctx)
    for i, c in enumerate(cands, 1):
        c["tpid"] = f"g{i}"
        c["node_name"] = _node_name(data, c.get("node") or "")
    for start in range(0, len(cands), TITLE_BATCH):
        batch = cands[start:start + TITLE_BATCH]
        try:
            obj = sm.ask_validated(llm, _title_prompt(profile, batch, scope_in),
                                   _title_validator({c["tpid"] for c in batch}, existing_titles),
                                   sm.RepairBudget(max_rounds))
        except sm.DigestFailed as exc:
            joblog.log(ctx.root, "plan", f"选题标题：本批（{len(batch)} 个）未通过，保留模板标题：{'；'.join(exc.issues[:2])}",
                       level="warn")
            continue
        by_tpid = {str(it["tpid"]): it for it in obj["items"]}
        for c in batch:
            it = by_tpid.get(c["tpid"])
            if it:
                c["title"] = str(it["title"]).strip()
                c["angle"] = str(it["angle"]).strip()
                c["kind"] = it["kind"]
                c["level"] = it["level"]
                c["titled_by"] = "model"
        joblog.log(ctx.root, "plan", f"选题标题：本批 {len(batch)} 个已由模型撰写")

def _ok_existing(ctx):
    out = []
    for row in catalog.list_articles(ctx):
        rec = drunner.load_digest(ctx, row["id"]) or {}
        if rec.get("status") == "ok":
            out.append({"id": row["id"], "title": rec.get("title") or row["title"],
                        "keys": dedup.concept_keys(rec.get("concepts") or [])})
    return out


def _mid_prompt(batch):
    lines = [f"{c['pid']}：候选「{c['title']}」，核心概念：{'、'.join(c['must_cover'])}；"
             f"与已有文章「{c['near_title']}」概念重叠 {c['near_j']:.2f}" for c in batch]
    return ("【阶段:选题判重】下面每个候选选题与已有文章的概念有一定重叠。判断已有文章是否已经"
            "充分讲清该候选的主题（实质重复）。若只是提及或角度不同，判 false。只输出 YAML：\n"
            "decisions:\n  - {id: p1, duplicate: true|false}\n必须对每个 id 给出判断。\n\n" + "\n".join(lines))


def _mid_validator(expected):
    def _v(obj):
        dec = obj.get("decisions")
        if not isinstance(dec, list):
            return ["decisions 应为列表"]
        got = {}
        for d in dec:
            if not isinstance(d, dict) or not isinstance(d.get("duplicate"), bool):
                return ["每条应含 id 与 duplicate（true/false）"]
            got[str(d.get("id"))] = d["duplicate"]
        miss = expected - set(got)
        return [f"缺少判断：{', '.join(sorted(miss))}"] if miss else []
    return _v


def _open_prompt(data, titles, scope_in, scope_out):
    tops = [f"- {nid} ｜ {n['name']}" for nid, n in tree.nodes_at(data, tree.TOPIC)]
    return ("【阶段:开放命题】在章程范围内提出最多 3 个值得写的新选题，它们应当填补现有文章没有覆盖的空白。"
            "不要与已有标题重复。只输出 YAML：\ntopics:\n"
            "  - {title: 标题, node: 主题id, kind: 概念讲解|对比|教程|综述|FAQ|案例, level: 入门|进阶|专题,\n"
            "     must_cover: [概念名…], angle: 角度}\n\n"
            f"范围：{scope_in or '不限'}；排除：{scope_out or '无'}\n已有主题：\n" + "\n".join(tops) +
            "\n已有文章标题：\n" + "\n".join(f"- {t}" for t in titles))


def _open_validator(data):
    topic_ids = {nid for nid, _ in tree.nodes_at(data, tree.TOPIC)}

    def _v(obj):
        items = obj.get("topics")
        if not isinstance(items, list):
            return ["topics 应为列表"]
        issues = []
        for it in items:
            if not isinstance(it, dict) or not str(it.get("title") or "").strip():
                issues.append("每个选题需含 title")
                continue
            if str(it.get("node")) not in topic_ids:
                issues.append(f"「{it.get('title')}」的 node 必须是已有主题的 id")
            if it.get("kind") not in KINDS or it.get("level") not in LEVELS:
                issues.append(f"「{it.get('title')}」的 kind/level 不合法")
            if not isinstance(it.get("must_cover"), list) or not it["must_cover"]:
                issues.append(f"「{it.get('title')}」需要 must_cover 列表")
        return issues
    return _v


def run(ctx, llm=None, *, open_topics: Optional[bool] = None) -> Dict[str, Any]:
    with joblog.run(ctx.root, "plan", "缺口与选题"):
        return _run(ctx, llm, open_topics=open_topics)


def _run(ctx, llm=None, *, open_topics: Optional[bool] = None) -> Dict[str, Any]:
    eff, _ = settings.effective_settings(ctx)
    budget = eff["budget"]["max_topics_per_cycle"]
    gear = eff["automation"]["gear"]
    auto_ok = eff["automation"]["auto_approve"] and gear in AUTO_GEARS
    if open_topics is None:
        open_topics = eff["automation"]["open_topics"]
    max_rounds = eff["budget"]["max_repair_rounds"]

    charter = ctx.load_charter()[0]
    scope_in = charter.get("scope", {}).get("in") or []
    scope_out = charter.get("scope", {}).get("out") or []
    data = tree.load(ctx)
    names = {m["id"]: m["name"] for m in model.list_concepts(ctx)}
    all_gaps = gaps.detect(ctx)
    store.write_json_atomic(ctx.path("plan", "gaps.json"), {"generated_at": _now(), "gaps": all_gaps})

    queue = states.load_topics(ctx)
    active_keys = {r.get("gap_key") for r in queue if r["status"] in (states.PROPOSED, states.APPROVED)}
    rejected_keys = {r.get("gap_key") for r in states.load_rejected(ctx)}
    existing = _ok_existing(ctx)
    active_titles = [r["title"] for r in queue if r["status"] in (states.PROPOSED, states.APPROVED)]

    dropped: Dict[str, int] = {}

    def drop(reason):
        dropped[reason] = dropped.get(reason, 0) + 1

    cands: List[Dict[str, Any]] = []
    for g in all_gaps:
        if g["key"] in active_keys:
            drop("已在队列中")
            continue
        if g["key"] in rejected_keys:
            drop("已拒绝过")
            continue
        if g.get("node") and not gaps.is_in_scope_node(data, charter, g["node"]):
            drop("超出范围")
            continue
        cands.append(template(g, names, data))

    if open_topics and llm is not None:
        cands += _open_candidates(ctx, llm, data, scope_in, scope_out, max_rounds, drop)

    kept, mid = [], []
    for c in cands:
        ck = dedup.concept_keys(c["must_cover"])
        out_terms = [vu.key(x) for x in scope_out if vu.key(x)]
        text_keys = vu.key(c["title"] + "".join(c["must_cover"]))
        if any(t in text_keys for t in out_terms):
            drop("超出范围")
            continue
        best_j, best = 0.0, None
        for e in existing:
            j = dedup.jaccard(ck, e["keys"])
            if j > best_j:
                best_j, best = j, e
        tsim = max([dedup.title_similarity(c["title"], e["title"]) for e in existing]
                   + [dedup.title_similarity(c["title"], t) for t in active_titles] + [0.0])
        c["_j"], c["_tsim"], c["_near"] = best_j, tsim, best
        if best_j >= dedup.HIGH:
            drop("与已有文章概念高度重复")
        elif tsim >= dedup.TITLE_HIGH:
            drop("标题与已有内容或待办相似")
        elif best_j >= dedup.MID:
            mid.append(c)
        else:
            kept.append(c)

    pending_mid = 0
    if mid:
        if llm is None:
            for c in mid:
                c["flags"] = ["未判重（需模型）"]
                kept.append(c)
            pending_mid = len(mid)
        else:
            for start in range(0, len(mid), 20):
                batch = mid[start:start + 20]
                items = [{"pid": f"p{i + 1}", **c} for i, c in enumerate(batch)]
                for it in items:
                    it["near_title"] = (it["_near"] or {}).get("title", "")
                    it["near_j"] = it["_j"]
                obj = sm.ask_validated(llm, _mid_prompt(items), _mid_validator({i["pid"] for i in items}),
                                       sm.RepairBudget(max_rounds))
                dup = {str(d["id"]): d["duplicate"] for d in obj["decisions"]}
                for it, orig in zip(items, batch):
                    if dup.get(it["pid"]):
                        drop("模型判定实质重复")
                    else:
                        kept.append(orig)

    for c in kept:
        imp = 1 + min(len(c["links"]), 5) / 5
        evid = min(1.0, 0.5 + 0.1 * len(c["links"]))
        overlap = max(c["_j"], c["_tsim"])
        value = round(c["severity"] * imp * evid - overlap, 2)
        c["score"] = value
        c["breakdown"] = {"severity": c["severity"], "importance": round(imp, 2), "evidence": round(evid, 2),
                          "overlap": round(overlap, 2)}
    # 只为最终会被提出的缺口选题撰写标题：被去重淘汰的候选不消耗模型调用，无变化时保持零调用
    to_title = [c for c in kept if c.get("gap_type") != "OPEN"]
    _write_titles(ctx, llm, to_title, data, scope_in, [r["title"] for r in catalog.list_articles(ctx)], max_rounds)
    kept.sort(key=lambda c: (-c["score"], c["title"]))
    take = kept[:budget] if budget else []
    for c in kept[budget:]:
        drop("超出每轮上限")

    new_rows = []
    for c in take:
        tid = states.alloc_topic_id(ctx)
        status = states.APPROVED if auto_ok else states.PROPOSED
        row = {"id": tid, "title": c["title"], "kind": c["kind"], "level": c["level"], "node": c.get("node"),
               "gap_key": c["gap_key"], "gap_type": c["gap_type"], "reason": c["gap_reason"],
               "must_cover": c["must_cover"], "links": c["links"], "angle": c.get("angle", ""),
               "score": c["score"], "breakdown": c["breakdown"], "flags": c.get("flags", []),
               "status": status, "created_at": _now(), "updated_at": _now(),
               "history": [{"from": None, "to": status, "at": _now(),
                            "note": "自动放行" if auto_ok else "等待人工放行"}]}
        new_rows.append(row)
    states.save_topics(ctx, queue + new_rows)
    return {"gaps": len(all_gaps), "proposed": len(new_rows), "dropped": dropped,
            "pending_mid": pending_mid, "auto_approved": auto_ok and bool(new_rows)}


def _open_candidates(ctx, llm, data, scope_in, scope_out, max_rounds, drop):
    titles = [r["title"] for r in catalog.list_articles(ctx)]
    obj = sm.ask_validated(llm, _open_prompt(data, titles, scope_in, scope_out), _open_validator(data),
                           sm.RepairBudget(max_rounds))
    out = []
    for it in obj["topics"][:3]:
        gap_key = "OPEN:" + hashlib.sha256(str(it["title"]).encode("utf-8")).hexdigest()[:10]
        out.append({"gap_key": gap_key, "gap_type": "OPEN", "gap_reason": "开放命题：" + str(it.get("angle", "")),
                    "node": str(it["node"]), "title": str(it["title"]).strip(), "kind": it["kind"],
                    "level": it["level"], "must_cover": [str(x) for x in it["must_cover"]],
                    "links": [], "angle": str(it.get("angle", "")), "severity": 1.0})
    return out
