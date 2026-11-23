"""planning/gaps.py — 缺口检测（方案 §6.1）。纯规则、不调用模型，每一条缺口都可以审计。

  G1 结构空洞   主题没有文章（红）或只有浅文（黄）
  G2 悬空概念   概念被 ≥ 2 篇文章提到，但没有任何标题以它为主题的文章
  G3 前置缺失   概念 A 依赖概念 B（prerequisite 边），而 B 没有入门文章讲它
  G4 对比空缺   两个概念（related/contrasts 边）共同出现在 ≥ 2 篇文章中，但没有对比文章
  G5 层级断档   某主题有进阶/专题文章，却没有入门文章
  G6 聚合缺失   某主题已有 ≥ 3 篇文章，却没有综述
每条缺口带：类型、主体（节点或概念 id）、严重度、理由、依据文章。
"""

from __future__ import annotations

from typing import Any, Dict, List, Set

from ..digest import runner as drunner
from ..library import catalog
from ..wiki import coverage, model, tree
from ..wiki import validate_util as vu

SEVERITY = {"G1": 3.0, "G1s": 2.0, "G2": 2.0, "G3": 3.0, "G4": 1.5, "G5": 2.0, "G6": 1.0}
TOPIC_MIN_FOR_SUMMARY = 3


def detect(ctx) -> List[Dict[str, Any]]:
    data = tree.load(ctx)
    cov = coverage.compute(ctx)
    rows = {r["id"]: r for r in catalog.list_articles(ctx)}
    digests = {aid: drunner.load_digest(ctx, aid) or {} for aid in rows}
    ok = {aid for aid, d in digests.items() if d.get("status") == "ok"}
    pages = {m["id"]: m for m in model.list_concepts(ctx)}
    names = {cid: m["name"] for cid, m in pages.items()}
    edges = model.read_jsonl(ctx, "edges.jsonl")

    def title(aid):
        return rows[aid]["title"]

    def node_articles(nid: str) -> Set[str]:
        arts: Set[str] = set()
        for cid in coverage._concepts_under(data, nid):
            arts |= set((pages.get(cid) or {}).get("articles") or [])
        return {a for a in arts if a in rows}

    def mentions(cid: str) -> Set[str]:
        return {a for a in (pages.get(cid) or {}).get("articles") or [] if a in rows}

    gaps: List[Dict[str, Any]] = []

    def gap(gtype, key_subject, node, name, reason, arts, concepts=()):
        gaps.append({"key": f"{gtype}:{key_subject}", "type": gtype, "subject": key_subject,
                     "node": node, "name": name, "severity": SEVERITY[gtype],
                     "reason": reason, "articles": sorted(arts), "concepts": list(concepts)})

    # G1 / G5 / G6：按主题节点
    for nid, n in tree.nodes_at(data, tree.TOPIC):
        m = cov["nodes"].get(nid, {})
        arts = node_articles(nid)
        concept_names = [names[c] for c in n.get("concepts") or [] if c in names]
        if len(arts) == 0:
            gap("G1", nid, nid, n["name"], f"主题「{n['name']}」还没有文章", arts, concept_names)
        elif m.get("color") == "yellow":
            gap("G1s", nid, nid, n["name"],
                f"主题「{n['name']}」只有 {len(arts)} 篇、平均 {m.get('avg_chars', 0)} 字，内容偏浅", arts, concept_names)
        levels = set(m.get("levels", {}))
        if levels and "入门" not in levels and levels & {"进阶", "专题"}:
            gap("G5", nid, nid, n["name"], f"主题「{n['name']}」有进阶/专题文章，却没有入门文章", arts, concept_names)
        kinds = {digests[a].get("kind") for a in arts}
        if len(arts) >= TOPIC_MIN_FOR_SUMMARY and "综述" not in kinds:
            gap("G6", nid, nid, n["name"], f"主题「{n['name']}」已有 {len(arts)} 篇文章，缺少综述", arts, concept_names)

    def node_of(cid):
        return tree.placed_concepts(data).get(cid)

    # G2：被多篇提到，但没有以它为标题的文章
    for cid, m in pages.items():
        arts = mentions(cid)
        if len(arts) < 2:
            continue
        if any(vu.appears(names[cid], title(a)) for a in rows):
            continue
        gap("G2", cid, node_of(cid), names[cid], f"「{names[cid]}」被 {len(arts)} 篇文章提到，但没有专文讲解", arts, [names[cid]])

    # G3：前置概念没有入门讲解
    for e in edges:
        if e.get("type") != "prerequisite":
            continue
        dst = e["dst"]
        if dst not in pages:
            continue
        taught = any(vu.appears(names[dst], title(a)) and digests[a].get("level") == "入门"
                     for a in rows if a in ok)
        if not taught:
            gap("G3", dst, node_of(dst), names[dst],
                f"「{names[e['src']]}」依赖「{names[dst]}」，但库中没有「{names[dst]}」的入门文章",
                mentions(dst), [names[dst]])

    # G4：常被一起提到的两个概念，没有对比文章
    seen_pairs = set()
    for e in edges:
        if e.get("type") not in ("related", "contrasts"):
            continue
        a, b = sorted((e["src"], e["dst"]))
        if (a, b) in seen_pairs or a not in pages or b not in pages:
            continue
        seen_pairs.add((a, b))
        both = mentions(a) & mentions(b)
        if len(both) < 2:
            continue
        compared = any(digests[x].get("kind") == "对比" and vu.appears(names[a], title(x))
                       and vu.appears(names[b], title(x)) for x in rows)
        if not compared:
            gap("G4", f"{a}|{b}", None, f"{names[a]} 与 {names[b]}",
                f"「{names[a]}」与「{names[b]}」在 {len(both)} 篇文章中一起出现，但没有对比文章",
                both, [names[a], names[b]])
    return gaps


def is_in_scope_node(data, charter, nid) -> bool:
    scope_in = charter.get("scope", {}).get("in") or []
    if not scope_in or nid is None:
        return True
    node = data["nodes"].get(nid)
    if not node:
        return True
    top = node if node.get("level") == tree.DIRECTION else data["nodes"].get(node.get("parent"), {})
    return top.get("name") in scope_in
